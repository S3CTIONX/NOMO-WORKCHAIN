// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title MilestoneManager
 * @notice Manages post-creation milestone changes for WorkChain jobs.
 *
 * Rules:
 *   - Milestones set at job creation are fixed (handled by WorkChain.sol)
 *   - Employer can PROPOSE adding a new milestone after job starts
 *   - Worker must APPROVE the proposal before it becomes active
 *   - Approved milestones are registered back into WorkChain
 *   - Employer must deposit the additional milestone amount when proposing
 *   - If worker rejects or proposal expires, deposit is refunded to employer
 *   - Neither party can unilaterally add scope — mutual consent enforced on-chain
 *
 * Integration:
 *   WorkChain.sol must authorise this contract to call addMilestoneFromManager()
 *   Deploy order: WorkChain → MilestoneManager → WorkChain.setMilestoneManager()
 */

interface IWorkChainMilestone {
    function addMilestoneFromManager(
        uint256 jobId,
        string calldata description,
        uint256 amount
    ) external payable;

    function getJob(uint256 jobId) external view returns (
        address employer,
        address worker,
        string memory title,
        uint256 totalEscrowed,
        uint256 totalReleased,
        uint8 status,
        uint8 workerRating,
        bool ratingSubmitted,
        uint256 createdAt,
        uint256 milestoneCount
    );
}

contract MilestoneManager {

    // ─────────────────────────────────────────────────────────────────────────
    // Types
    // ─────────────────────────────────────────────────────────────────────────

    enum ProposalStatus {
        Pending,    // Submitted by employer, awaiting worker response
        Approved,   // Worker approved — milestone added to WorkChain
        Rejected,   // Worker rejected — deposit refunded
        Expired,    // Proposal window passed — deposit refunded
        Cancelled   // Employer cancelled before worker responded
    }

    struct MilestoneProposal {
        uint256   jobId;
        address   employer;
        address   worker;
        string    description;
        uint256   amount;           // MON locked by employer on proposal
        uint256   proposedAt;
        uint256   expiresAt;        // Proposal window — default 72 hours
        ProposalStatus status;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // State
    // ─────────────────────────────────────────────────────────────────────────

    address public owner;
    IWorkChainMilestone public workchain;

    uint256 public proposalCount;
    uint256 public proposalWindow = 72 hours;   // Worker has 72h to respond

    mapping(uint256 => MilestoneProposal) public proposals;

    // jobId → proposalIds — track all proposals per job
    mapping(uint256 => uint256[]) public jobProposals;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event MilestoneProposed(
        uint256 indexed proposalId,
        uint256 indexed jobId,
        address indexed employer,
        string  description,
        uint256 amount,
        uint256 expiresAt
    );

    event MilestoneApproved(
        uint256 indexed proposalId,
        uint256 indexed jobId,
        address indexed worker
    );

    event MilestoneRejected(
        uint256 indexed proposalId,
        uint256 indexed jobId,
        address indexed worker
    );

    event ProposalExpired(
        uint256 indexed proposalId,
        uint256 indexed jobId
    );

    event ProposalCancelled(
        uint256 indexed proposalId,
        uint256 indexed jobId,
        address indexed employer
    );

    event ProposalWindowUpdated(uint256 oldWindow, uint256 newWindow);

    // ─────────────────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "MilestoneManager: not owner");
        _;
    }

    modifier proposalExists(uint256 _proposalId) {
        require(_proposalId < proposalCount, "MilestoneManager: proposal does not exist");
        _;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    constructor(address _workchain) {
        require(_workchain != address(0), "MilestoneManager: invalid workchain");
        owner     = msg.sender;
        workchain = IWorkChainMilestone(_workchain);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Employer: propose a new milestone
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Employer proposes adding a new milestone to an active job.
     *         Employer must send the milestone amount as msg.value — locked here
     *         until worker approves, rejects, or proposal expires.
     *
     * @param _jobId        Job ID in WorkChain
     * @param _description  Milestone description
     */
    function proposeMilestone(
        uint256 _jobId,
        string calldata _description
    ) external payable returns (uint256 proposalId) {
        require(msg.value > 0,                  "MilestoneManager: amount required");
        require(bytes(_description).length > 0,  "MilestoneManager: description required");
        require(bytes(_description).length <= 256, "MilestoneManager: description too long");

        // Fetch job from WorkChain and validate caller is employer
        (address employer, address worker, , , , uint8 status, , , ,) = workchain.getJob(_jobId);
        require(msg.sender == employer, "MilestoneManager: not employer");
        require(status == 0,            "MilestoneManager: job not active"); // 0 = Active

        proposalId = proposalCount++;
        uint256 expiry = block.timestamp + proposalWindow;

        proposals[proposalId] = MilestoneProposal({
            jobId:       _jobId,
            employer:    employer,
            worker:      worker,
            description: _description,
            amount:      msg.value,
            proposedAt:  block.timestamp,
            expiresAt:   expiry,
            status:      ProposalStatus.Pending
        });

        jobProposals[_jobId].push(proposalId);

        emit MilestoneProposed(proposalId, _jobId, employer, _description, msg.value, expiry);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Worker: approve or reject
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Worker approves a pending proposal.
     *         Triggers addMilestoneFromManager() on WorkChain — forwarding the locked funds.
     *         Milestone becomes active immediately.
     */
    function approveMilestone(uint256 _proposalId)
        external
        proposalExists(_proposalId)
    {
        MilestoneProposal storage p = proposals[_proposalId];
        require(msg.sender == p.worker,             "MilestoneManager: not worker");
        require(p.status == ProposalStatus.Pending, "MilestoneManager: not pending");
        require(block.timestamp <= p.expiresAt,     "MilestoneManager: proposal expired");

        p.status = ProposalStatus.Approved;

        emit MilestoneApproved(_proposalId, p.jobId, msg.sender);

        // Forward locked funds to WorkChain — milestone added to escrow
        workchain.addMilestoneFromManager{value: p.amount}(
            p.jobId,
            p.description,
            p.amount
        );
    }

    /**
     * @notice Worker rejects a pending proposal.
     *         Locked funds are refunded to the employer immediately.
     */
    function rejectMilestone(uint256 _proposalId)
        external
        proposalExists(_proposalId)
    {
        MilestoneProposal storage p = proposals[_proposalId];
        require(msg.sender == p.worker,             "MilestoneManager: not worker");
        require(p.status == ProposalStatus.Pending, "MilestoneManager: not pending");
        require(block.timestamp <= p.expiresAt,     "MilestoneManager: proposal expired");

        p.status = ProposalStatus.Rejected;

        emit MilestoneRejected(_proposalId, p.jobId, msg.sender);

        // Refund employer
        (bool ok,) = p.employer.call{value: p.amount}("");
        require(ok, "MilestoneManager: refund failed");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Employer: cancel before worker responds
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Employer cancels a pending proposal and gets deposit back.
     *         Can only cancel if worker has not yet responded.
     */
    function cancelProposal(uint256 _proposalId)
        external
        proposalExists(_proposalId)
    {
        MilestoneProposal storage p = proposals[_proposalId];
        require(msg.sender == p.employer,           "MilestoneManager: not employer");
        require(p.status == ProposalStatus.Pending, "MilestoneManager: not pending");

        p.status = ProposalStatus.Cancelled;

        emit ProposalCancelled(_proposalId, p.jobId, msg.sender);

        (bool ok,) = p.employer.call{value: p.amount}("");
        require(ok, "MilestoneManager: refund failed");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Anyone: expire a stale proposal and refund employer
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Any caller can expire a proposal that has passed its window.
     *         Employer gets their deposit back. Worker's silence = rejection.
     */
    function expireProposal(uint256 _proposalId)
        external
        proposalExists(_proposalId)
    {
        MilestoneProposal storage p = proposals[_proposalId];
        require(p.status == ProposalStatus.Pending,  "MilestoneManager: not pending");
        require(block.timestamp > p.expiresAt,       "MilestoneManager: not yet expired");

        p.status = ProposalStatus.Expired;

        emit ProposalExpired(_proposalId, p.jobId);

        (bool ok,) = p.employer.call{value: p.amount}("");
        require(ok, "MilestoneManager: refund failed");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Read functions
    // ─────────────────────────────────────────────────────────────────────────

    function getProposal(uint256 _proposalId)
        external
        view
        proposalExists(_proposalId)
        returns (MilestoneProposal memory)
    {
        return proposals[_proposalId];
    }

    function getJobProposals(uint256 _jobId)
        external
        view
        returns (uint256[] memory)
    {
        return jobProposals[_jobId];
    }

    function getPendingProposals(uint256 _jobId)
        external
        view
        returns (uint256[] memory pendingIds)
    {
        uint256[] storage all = jobProposals[_jobId];
        uint256 count;
        for (uint256 i; i < all.length; i++) {
            if (proposals[all[i]].status == ProposalStatus.Pending) count++;
        }
        pendingIds = new uint256[](count);
        uint256 idx;
        for (uint256 i; i < all.length; i++) {
            if (proposals[all[i]].status == ProposalStatus.Pending) {
                pendingIds[idx++] = all[i];
            }
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Admin
    // ─────────────────────────────────────────────────────────────────────────

    function setProposalWindow(uint256 _seconds) external onlyOwner {
        require(_seconds >= 1 hours,  "MilestoneManager: minimum 1 hour");
        require(_seconds <= 30 days,  "MilestoneManager: maximum 30 days");
        emit ProposalWindowUpdated(proposalWindow, _seconds);
        proposalWindow = _seconds;
    }

    function setWorkChain(address _workchain) external onlyOwner {
        require(_workchain != address(0), "MilestoneManager: invalid address");
        workchain = IWorkChainMilestone(_workchain);
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "MilestoneManager: invalid owner");
        owner = _newOwner;
    }

    receive() external payable {}
}
