// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title WorkChain (v2 — Registry-aware)
 * @notice On-chain labor escrow for the Nigerian freelance market.
 *         Built for Monad.
 *
 * Changes from v1:
 *   - authorizedRegistry: address of VerificationRegistry
 *   - releaseMilestoneFromRegistry(): callable ONLY by the registry
 *     after verification passes — enables automatic fund release
 *   - releaseMilestone() still exists for manual employer release
 *     (employer can always release directly without verification)
 */
contract WorkChain {

    // ─────────────────────────────────────────────────────────────────────────
    // Types
    // ─────────────────────────────────────────────────────────────────────────

    enum Status { Active, Complete, Disputed }

    struct Milestone {
        string  description;
        uint256 amount;
        bool    released;
        bool    verified;     // True if released via VerificationRegistry
    }

    struct Job {
        address   employer;
        address   worker;
        string    title;
        uint256   totalEscrowed;
        uint256   totalReleased;
        Status    status;
        uint8     workerRating;
        bool      ratingSubmitted;
        uint256   createdAt;
        Milestone[] milestones;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // State
    // ─────────────────────────────────────────────────────────────────────────

    address public owner;
    address public authorizedRegistry;    // VerificationRegistry — only address that can auto-release

    uint256 public jobCount;
    mapping(uint256 => Job) private jobs;
    mapping(address => uint256[]) public workerJobs;
    mapping(address => uint256[]) public employerJobs;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event JobCreated(uint256 indexed jobId, address indexed employer, address indexed worker, string title, uint256 total);
    event MilestoneReleased(uint256 indexed jobId, uint256 milestoneIndex, uint256 amount, address worker, bool autoReleased);
    event DisputeRaised(uint256 indexed jobId, address raisedBy);
    event JobCompleted(uint256 indexed jobId);
    event RatingSubmitted(uint256 indexed jobId, address indexed worker, uint8 score);
    event RegistryUpdated(address indexed oldRegistry, address indexed newRegistry);

    // ─────────────────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "WorkChain: not owner");
        _;
    }

    modifier onlyEmployer(uint256 _jobId) {
        require(msg.sender == jobs[_jobId].employer, "WorkChain: not employer");
        _;
    }

    modifier onlyParty(uint256 _jobId) {
        require(
            msg.sender == jobs[_jobId].employer || msg.sender == jobs[_jobId].worker,
            "WorkChain: not a party"
        );
        _;
    }

    modifier onlyRegistry() {
        require(msg.sender == authorizedRegistry, "WorkChain: not authorized registry");
        _;
    }

    modifier isActive(uint256 _jobId) {
        require(jobs[_jobId].status == Status.Active, "WorkChain: not active");
        _;
    }

    modifier jobExists(uint256 _jobId) {
        require(_jobId < jobCount, "WorkChain: job does not exist");
        _;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Write functions
    // ─────────────────────────────────────────────────────────────────────────

    function createJob(
        address _worker,
        string calldata _title,
        string[] calldata _descriptions,
        uint256[] calldata _amounts
    ) external payable returns (uint256 jobId) {
        require(_worker != address(0),                   "WorkChain: invalid worker");
        require(_worker != msg.sender,                   "WorkChain: employer cannot be worker");
        require(_descriptions.length == _amounts.length, "WorkChain: array mismatch");
        require(_descriptions.length > 0 && _descriptions.length <= 20, "WorkChain: 1-20 milestones");

        uint256 total;
        for (uint256 i; i < _amounts.length; i++) {
            require(_amounts[i] > 0, "WorkChain: amount must be > 0");
            total += _amounts[i];
        }
        require(msg.value == total, "WorkChain: value must equal total");

        jobId = jobCount++;
        Job storage j = jobs[jobId];
        j.employer      = msg.sender;
        j.worker        = _worker;
        j.title         = _title;
        j.totalEscrowed = total;
        j.status        = Status.Active;
        j.createdAt     = block.timestamp;

        for (uint256 i; i < _descriptions.length; i++) {
            j.milestones.push(Milestone({
                description: _descriptions[i],
                amount:      _amounts[i],
                released:    false,
                verified:    false
            }));
        }

        workerJobs[_worker].push(jobId);
        employerJobs[msg.sender].push(jobId);
        emit JobCreated(jobId, msg.sender, _worker, _title, total);
    }

    /**
     * @notice Employer manually releases a milestone (no verification required).
     *         Employer always retains the ability to release directly.
     */
    function releaseMilestone(uint256 _jobId, uint256 _milestoneIndex)
        external
        jobExists(_jobId)
        onlyEmployer(_jobId)
        isActive(_jobId)
    {
        _release(_jobId, _milestoneIndex, false);
    }

    /**
     * @notice Called ONLY by VerificationRegistry after proof is confirmed.
     *         Automatically releases milestone funds to worker.
     *         The registry has already validated:
     *           - worker submitted a proof
     *           - backend confirmed the proof hash matches
     */
    function releaseMilestoneFromRegistry(uint256 _jobId, uint256 _milestoneIndex)
        external
        onlyRegistry
        jobExists(_jobId)
        isActive(_jobId)
    {
        jobs[_jobId].milestones[_milestoneIndex].verified = true;
        _release(_jobId, _milestoneIndex, true);
    }

    /**
     * @dev Internal release logic — shared by both release paths.
     *      autoReleased = true if triggered by registry, false if employer.
     */
    function _release(uint256 _jobId, uint256 _milestoneIndex, bool _autoReleased) internal {
        Job storage j = jobs[_jobId];
        require(_milestoneIndex < j.milestones.length, "WorkChain: invalid index");
        require(!j.milestones[_milestoneIndex].released, "WorkChain: already released");

        uint256 amount = j.milestones[_milestoneIndex].amount;
        j.milestones[_milestoneIndex].released = true;
        j.totalReleased += amount;

        (bool ok,) = j.worker.call{value: amount}("");
        require(ok, "WorkChain: transfer failed");

        emit MilestoneReleased(_jobId, _milestoneIndex, amount, j.worker, _autoReleased);

        if (j.totalReleased == j.totalEscrowed) {
            j.status = Status.Complete;
            emit JobCompleted(_jobId);
        }
    }

    function raiseDispute(uint256 _jobId)
        external
        jobExists(_jobId)
        onlyParty(_jobId)
        isActive(_jobId)
    {
        jobs[_jobId].status = Status.Disputed;
        emit DisputeRaised(_jobId, msg.sender);
    }

    function submitRating(uint256 _jobId, uint8 _score)
        external
        jobExists(_jobId)
        onlyEmployer(_jobId)
    {
        Job storage j = jobs[_jobId];
        require(j.status == Status.Complete, "WorkChain: not complete");
        require(!j.ratingSubmitted,          "WorkChain: already rated");
        require(_score >= 1 && _score <= 5,  "WorkChain: score 1-5");
        j.workerRating    = _score;
        j.ratingSubmitted = true;
        emit RatingSubmitted(_jobId, j.worker, _score);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Admin
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Set or update the authorized VerificationRegistry address.
     *         Call this after deploying VerificationRegistry.
     *         Deploy order: WorkChain → VerificationRegistry → setRegistry()
     */
    function setRegistry(address _registry) external onlyOwner {
        require(_registry != address(0), "WorkChain: invalid registry");
        emit RegistryUpdated(authorizedRegistry, _registry);
        authorizedRegistry = _registry;
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "WorkChain: invalid owner");
        owner = _newOwner;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Read functions
    // ─────────────────────────────────────────────────────────────────────────

    function getJob(uint256 _jobId) external view jobExists(_jobId) returns (
        address employer, address worker, string memory title,
        uint256 totalEscrowed, uint256 totalReleased,
        Status status, uint8 workerRating, bool ratingSubmitted,
        uint256 createdAt, uint256 milestoneCount
    ) {
        Job storage j = jobs[_jobId];
        return (j.employer, j.worker, j.title, j.totalEscrowed, j.totalReleased,
                j.status, j.workerRating, j.ratingSubmitted, j.createdAt, j.milestones.length);
    }

    function getMilestones(uint256 _jobId) external view jobExists(_jobId) returns (Milestone[] memory) {
        return jobs[_jobId].milestones;
    }

    function getWorkerJobs(address _worker) external view returns (uint256[] memory) {
        return workerJobs[_worker];
    }

    function getEmployerJobs(address _employer) external view returns (uint256[] memory) {
        return employerJobs[_employer];
    }

    function escrowProgress(uint256 _jobId) external view jobExists(_jobId) returns (uint256) {
        Job storage j = jobs[_jobId];
        if (j.totalEscrowed == 0) return 0;
        return (j.totalReleased * 100) / j.totalEscrowed;
    }
}
