// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title DisputeResolver
 * @notice Centralised arbitration for WorkChain disputed jobs.
 *
 * V1 model — owner wallet arbitrates:
 *   1. Either party raises dispute on WorkChain (job frozen)
 *   2. WorkChain notifies this contract via openDispute()
 *   3. Both parties can submit evidence (text + hash, stored on-chain)
 *   4. Owner reviews evidence off-chain and calls resolve()
 *   5. resolve() specifies how the remaining escrow is split
 *   6. Funds transfer immediately — decision is final
 *
 * Resolution options:
 *   - Full award to worker (employer breached)
 *   - Full refund to employer (worker breached)
 *   - Any custom split (partial completion)
 *
 * V2 upgrade path: replace owner arbitration with a
 * multi-sig panel or token-weighted jury system.
 *
 * Integration:
 *   WorkChain.sol must call openDispute() when raiseDispute() is triggered.
 *   WorkChain must authorise this contract to call resolveFromDisputer().
 */

interface IWorkChainDispute {
    function resolveFromDisputer(
        uint256 jobId,
        address employer,
        address worker,
        uint256 employerAmount,
        uint256 workerAmount
    ) external;

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

contract DisputeResolver {

    // ─────────────────────────────────────────────────────────────────────────
    // Types
    // ─────────────────────────────────────────────────────────────────────────

    enum DisputeStatus {
        Open,       // Raised, awaiting evidence and resolution
        Resolved,   // Arbitrator ruled — funds distributed
        Dismissed   // Invalid dispute — job restored to Active
    }

    struct Evidence {
        address   submittedBy;
        string    description;      // Plain text summary of evidence
        bytes32   evidenceHash;     // keccak256 of supporting document (off-chain)
        string    evidenceURI;      // IPFS CID or link to supporting material
        uint256   submittedAt;
    }

    struct Dispute {
        uint256       jobId;
        address       employer;
        address       worker;
        uint256       remainingEscrow;   // Funds available for distribution
        uint256       openedAt;
        uint256       resolvedAt;
        DisputeStatus status;
        address       resolvedBy;
        string        resolutionNote;    // Arbitrator's reasoning (on-chain)
        uint256       employerAward;     // How much employer received
        uint256       workerAward;       // How much worker received
        Evidence[]    evidence;          // All submitted evidence
    }

    // ─────────────────────────────────────────────────────────────────────────
    // State
    // ─────────────────────────────────────────────────────────────────────────

    address public owner;
    address public arbitrator;       // Can be same as owner or delegated wallet
    IWorkChainDispute public workchain;

    uint256 public disputeCount;
    uint256 public evidenceWindow = 48 hours;   // Window for parties to submit evidence

    // jobId → disputeId (one active dispute per job at a time)
    mapping(uint256 => uint256) public jobToDispute;
    mapping(uint256 => bool)    public jobHasDispute;

    mapping(uint256 => Dispute) private disputes;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event DisputeOpened(
        uint256 indexed disputeId,
        uint256 indexed jobId,
        address indexed raisedBy,
        uint256 remainingEscrow
    );

    event EvidenceSubmitted(
        uint256 indexed disputeId,
        uint256 indexed jobId,
        address indexed submittedBy,
        bytes32 evidenceHash
    );

    event DisputeResolved(
        uint256 indexed disputeId,
        uint256 indexed jobId,
        address indexed resolvedBy,
        uint256 employerAward,
        uint256 workerAward,
        string  resolutionNote
    );

    event DisputeDismissed(
        uint256 indexed disputeId,
        uint256 indexed jobId,
        string  reason
    );

    event ArbitratorUpdated(address indexed oldArbitrator, address indexed newArbitrator);

    // ─────────────────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "DisputeResolver: not owner");
        _;
    }

    modifier onlyArbitrator() {
        require(
            msg.sender == arbitrator || msg.sender == owner,
            "DisputeResolver: not arbitrator"
        );
        _;
    }

    modifier onlyWorkChain() {
        require(msg.sender == address(workchain), "DisputeResolver: not workchain");
        _;
    }

    modifier disputeExists(uint256 _disputeId) {
        require(_disputeId < disputeCount, "DisputeResolver: dispute does not exist");
        _;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    constructor(address _arbitrator, address _workchain) {
        require(_arbitrator != address(0), "DisputeResolver: invalid arbitrator");
        require(_workchain  != address(0), "DisputeResolver: invalid workchain");
        owner       = msg.sender;
        arbitrator  = _arbitrator;
        workchain   = IWorkChainDispute(_workchain);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // WorkChain: open a dispute
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Called by WorkChain when raiseDispute() is triggered.
     *         Receives the remaining locked escrow as msg.value.
     *         WorkChain transfers custody of disputed funds here.
     *
     * @param _jobId     Job ID
     * @param _raisedBy  Address that raised the dispute
     */
    function openDispute(uint256 _jobId, address _raisedBy)
        external
        payable
        onlyWorkChain
    {
        require(!jobHasDispute[_jobId], "DisputeResolver: dispute already open");
        require(msg.value > 0,          "DisputeResolver: no funds to dispute");

        (address employer, address worker, , , , , , , ,) = workchain.getJob(_jobId);

        uint256 disputeId = disputeCount++;

        Dispute storage d = disputes[disputeId];
        d.jobId           = _jobId;
        d.employer        = employer;
        d.worker          = worker;
        d.remainingEscrow = msg.value;
        d.openedAt        = block.timestamp;
        d.status          = DisputeStatus.Open;

        jobToDispute[_jobId]  = disputeId;
        jobHasDispute[_jobId] = true;

        emit DisputeOpened(disputeId, _jobId, _raisedBy, msg.value);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Parties: submit evidence
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Either party submits evidence during the evidence window.
     *         Evidence is stored on-chain permanently.
     *         Hash allows arbitrator to verify document integrity off-chain.
     *
     * @param _disputeId    Dispute ID
     * @param _description  Plain text description of the evidence
     * @param _evidenceHash keccak256 of the supporting document
     * @param _evidenceURI  IPFS CID or URL to supporting material
     */
    function submitEvidence(
        uint256 _disputeId,
        string calldata _description,
        bytes32 _evidenceHash,
        string calldata _evidenceURI
    ) external disputeExists(_disputeId) {
        Dispute storage d = disputes[_disputeId];
        require(d.status == DisputeStatus.Open,        "DisputeResolver: dispute not open");
        require(
            msg.sender == d.employer || msg.sender == d.worker,
            "DisputeResolver: not a party"
        );
        require(bytes(_description).length > 0,        "DisputeResolver: description required");
        require(_evidenceHash != bytes32(0),           "DisputeResolver: hash required");
        require(bytes(_evidenceURI).length > 0,        "DisputeResolver: URI required");
        require(d.evidence.length < 10,                "DisputeResolver: max 10 evidence items");

        d.evidence.push(Evidence({
            submittedBy:  msg.sender,
            description:  _description,
            evidenceHash: _evidenceHash,
            evidenceURI:  _evidenceURI,
            submittedAt:  block.timestamp
        }));

        emit EvidenceSubmitted(_disputeId, d.jobId, msg.sender, _evidenceHash);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Arbitrator: resolve
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Arbitrator resolves the dispute by specifying fund distribution.
     *         employerAmount + workerAmount must equal remainingEscrow exactly.
     *         Transfers happen immediately. Decision is final.
     *
     * @param _disputeId      Dispute ID
     * @param _employerAmount MON awarded to employer
     * @param _workerAmount   MON awarded to worker
     * @param _note           On-chain reasoning for the decision
     */
    function resolve(
        uint256 _disputeId,
        uint256 _employerAmount,
        uint256 _workerAmount,
        string calldata _note
    ) external onlyArbitrator disputeExists(_disputeId) {
        Dispute storage d = disputes[_disputeId];
        require(d.status == DisputeStatus.Open,                           "DisputeResolver: not open");
        require(_employerAmount + _workerAmount == d.remainingEscrow,     "DisputeResolver: amounts must equal escrow");
        require(bytes(_note).length > 0,                                  "DisputeResolver: note required");

        d.status          = DisputeStatus.Resolved;
        d.resolvedAt      = block.timestamp;
        d.resolvedBy      = msg.sender;
        d.resolutionNote  = _note;
        d.employerAward   = _employerAmount;
        d.workerAward     = _workerAmount;

        jobHasDispute[d.jobId] = false;

        emit DisputeResolved(
            _disputeId,
            d.jobId,
            msg.sender,
            _employerAmount,
            _workerAmount,
            _note
        );

        // Transfer employer share
        if (_employerAmount > 0) {
            (bool okE,) = d.employer.call{value: _employerAmount}("");
            require(okE, "DisputeResolver: employer transfer failed");
        }

        // Transfer worker share
        if (_workerAmount > 0) {
            (bool okW,) = d.worker.call{value: _workerAmount}("");
            require(okW, "DisputeResolver: worker transfer failed");
        }
    }

    /**
     * @notice Arbitrator dismisses an invalid dispute.
     *         Job is restored to Active on WorkChain.
     *         Funds returned to WorkChain escrow.
     *
     * @param _disputeId  Dispute ID
     * @param _reason     Why the dispute was dismissed
     */
    function dismiss(
        uint256 _disputeId,
        string calldata _reason
    ) external onlyArbitrator disputeExists(_disputeId) {
        Dispute storage d = disputes[_disputeId];
        require(d.status == DisputeStatus.Open, "DisputeResolver: not open");
        require(bytes(_reason).length > 0,      "DisputeResolver: reason required");

        d.status     = DisputeStatus.Dismissed;
        d.resolvedAt = block.timestamp;
        d.resolvedBy = msg.sender;

        jobHasDispute[d.jobId] = false;

        emit DisputeDismissed(_disputeId, d.jobId, _reason);

        // Return funds to WorkChain (restores escrow)
        workchain.resolveFromDisputer{value: d.remainingEscrow}(
            d.jobId,
            d.employer,
            d.worker,
            0,                    // employer gets nothing from resolver
            d.remainingEscrow     // full amount back to workchain escrow
        );
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Read functions
    // ─────────────────────────────────────────────────────────────────────────

    function getDispute(uint256 _disputeId)
        external
        view
        disputeExists(_disputeId)
        returns (
            uint256 jobId,
            address employer,
            address worker,
            uint256 remainingEscrow,
            uint256 openedAt,
            uint256 resolvedAt,
            DisputeStatus status,
            address resolvedBy,
            string memory resolutionNote,
            uint256 employerAward,
            uint256 workerAward,
            uint256 evidenceCount
        )
    {
        Dispute storage d = disputes[_disputeId];
        return (
            d.jobId, d.employer, d.worker, d.remainingEscrow,
            d.openedAt, d.resolvedAt, d.status, d.resolvedBy,
            d.resolutionNote, d.employerAward, d.workerAward,
            d.evidence.length
        );
    }

    function getEvidence(uint256 _disputeId, uint256 _index)
        external
        view
        disputeExists(_disputeId)
        returns (Evidence memory)
    {
        require(_index < disputes[_disputeId].evidence.length, "DisputeResolver: invalid index");
        return disputes[_disputeId].evidence[_index];
    }

    function getAllEvidence(uint256 _disputeId)
        external
        view
        disputeExists(_disputeId)
        returns (Evidence[] memory)
    {
        return disputes[_disputeId].evidence;
    }

    function getDisputeByJob(uint256 _jobId)
        external
        view
        returns (uint256 disputeId, bool exists)
    {
        return (jobToDispute[_jobId], jobHasDispute[_jobId]);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Admin
    // ─────────────────────────────────────────────────────────────────────────

    function setArbitrator(address _newArbitrator) external onlyOwner {
        require(_newArbitrator != address(0), "DisputeResolver: invalid arbitrator");
        emit ArbitratorUpdated(arbitrator, _newArbitrator);
        arbitrator = _newArbitrator;
    }

    function setWorkChain(address _workchain) external onlyOwner {
        require(_workchain != address(0), "DisputeResolver: invalid address");
        workchain = IWorkChainDispute(_workchain);
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "DisputeResolver: invalid owner");
        owner = _newOwner;
    }

    receive() external payable {}
}
