// a─────────────────────────────────────────────────────────────────────────
    // Types
    // ─────────────────────────────────────────────────────────────────────────

    enum VerificationStatus {
        None,       // No proof submitted yet
        Pending,    // Worker submitted — awaiting backend confirmation
        Verified,   // Backend confirmed — funds released
        Rejected    // Backend rejected — worker must resubmit
    }

    enum ProofType {
        GitHub,     // GitHub commit / PR link
        FileHash,   // IPFS hash or SHA256 of deliverable file
        Link,       // Any external URL (Figma, Loom, deployed site, etc.)
        Manual      // Free-text description — lowest trust, still requires verifier
    }

    struct Proof {
        address  submittedBy;       // Must be the job's worker
        bytes32  proofHash;         // keccak256 of the proof content (off-chain)
        string   proofURI;          // Human-readable: GitHub URL, IPFS CID, link, text
        ProofType proofType;
        uint256  submittedAt;
        uint256  confirmedAt;       // 0 if not yet confirmed
        VerificationStatus status;
        string   rejectionReason;   // Populated if Rejected
    }

    // ─────────────────────────────────────────────────────────────────────────
    // State
    // ─────────────────────────────────────────────────────────────────────────

    address public owner;
    address public verifier;          // Backend wallet — only address that can confirm/reject
    IWorkChain public workchain;      // WorkChain contract to call back on verification

    // jobId → milestoneIndex → Proof
    mapping(uint256 => mapping(uint256 => Proof)) public proofs;

    // Worker address → list of (jobId, milestoneIndex) as packed uint256
    // packed = jobId << 128 | milestoneIndex — for off-chain lookup
    mapping(address => uint256[]) public workerSubmissions;

    // Total counts for analytics
    uint256 public totalSubmitted;
    uint256 public totalVerified;
    uint256 public totalRejected;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event ProofSubmitted(
        uint256 indexed jobId,
        uint256 indexed milestoneIndex,
        address indexed worker,
        ProofType proofType,
        bytes32 proofHash,
        string proofURI
    );

    event ProofVerified(
        uint256 indexed jobId,
        uint256 indexed milestoneIndex,
        address indexed worker,
        address verifiedBy
    );

    event ProofRejected(
        uint256 indexed jobId,
        uint256 indexed milestoneIndex,
        address indexed worker,
        string reason
    );

    event VerifierUpdated(address indexed oldVerifier, address indexed newVerifier);
    event WorkChainUpdated(address indexed oldAddress, address indexed newAddress);

    // ─────────────────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Registry: not owner");
        _;
    }

    modifier onlyVerifier() {
        require(msg.sender == verifier, "Registry: not verifier");
        _;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @param _verifier   Backend wallet address — the server key that confirms proofs
     * @param _workchain  Deployed WorkChain contract address
     */
    constructor(address _verifier, address _workchain) {
        require(_verifier  != address(0), "Registry: invalid verifier");
        require(_workchain != address(0), "Registry: invalid workchain");

        owner     = msg.sender;
        verifier  = _verifier;
        workchain = IWorkChain(_workchain);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Worker: submit proof
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Worker submits a proof of completion for a milestone.
     *         The proof is stored on-chain as Pending.
     *         Backend picks this up via the ProofSubmitted event and verifies off-chain.
     *
     * @param _jobId           Job ID from WorkChain
     * @param _milestoneIndex  Index of the milestone being proved
     * @param _proofHash       keccak256(proofContent) — computed off-chain, stored on-chain
     *                         Allows verifier to confirm integrity without storing raw content
     * @param _proofURI        Human-readable proof: GitHub URL, IPFS CID, link, or text
     * @param _proofType       One of: GitHub, FileHash, Link, Manual
     */
    function submitProof(
        uint256  _jobId,
        uint256  _milestoneIndex,
        bytes32  _proofHash,
        string calldata _proofURI,
        ProofType _proofType
    ) external {
        require(_proofHash != bytes32(0),      "Registry: proof hash required");
        require(bytes(_proofURI).length > 0,   "Registry: proof URI required");
        require(bytes(_proofURI).length <= 512, "Registry: URI too long");

        Proof storage existing = proofs[_jobId][_milestoneIndex];

        // Allow resubmission only if previously Rejected
        require(
            existing.status == VerificationStatus.None ||
            existing.status == VerificationStatus.Rejected,
            "Registry: proof already pending or verified"
        );

        proofs[_jobId][_milestoneIndex] = Proof({
            submittedBy:     msg.sender,
            proofHash:       _proofHash,
            proofURI:        _proofURI,
            proofType:       _proofType,
            submittedAt:     block.timestamp,
            confirmedAt:     0,
            status:          VerificationStatus.Pending,
            rejectionReason: ""
        });

        // Pack jobId + milestoneIndex for off-chain indexing
        workerSubmissions[msg.sender].push((_jobId << 128) | _milestoneIndex);
        totalSubmitted++;

        emit ProofSubmitted(
            _jobId,
            _milestoneIndex,
            msg.sender,
            _proofType,
            _proofHash,
            _proofURI
        );
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Verifier: confirm or reject
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Backend verifier confirms a pending proof.
     *         Triggers automatic milestone release via WorkChain callback.
     *
     * @param _jobId           Job ID
     * @param _milestoneIndex  Milestone index
     * @param _expectedHash    Hash the backend computed independently — must match submission
     *                         Prevents the backend from confirming a proof it never saw
     */
    function confirmProof(
        uint256 _jobId,
        uint256 _milestoneIndex,
        bytes32 _expectedHash
    ) external onlyVerifier {
        Proof storage p = proofs[_jobId][_milestoneIndex];

        require(p.status == VerificationStatus.Pending, "Registry: proof not pending");
        require(p.proofHash == _expectedHash,           "Registry: hash mismatch");

        p.status      = VerificationStatus.Verified;
        p.confirmedAt = block.timestamp;
        totalVerified++;

        emit ProofVerified(_jobId, _milestoneIndex, p.submittedBy, msg.sender);

        // Callback → WorkChain releases milestone funds automatically
        // WorkChain must have this registry set as authorizedRegistry
        workchain.releaseMilestoneFromRegistry(_jobId, _milestoneIndex);
    }

    /**
     * @notice Backend rejects a proof — worker must resubmit with better evidence.
     *
     * @param _jobId           Job ID
     * @param _milestoneIndex  Milestone index
     * @param _reason          Plain-text reason shown to the worker
     */
    function rejectProof(
        uint256 _jobId,
        uint256 _milestoneIndex,
        string calldata _reason
    ) external onlyVerifier {
        require(bytes(_reason).length > 0, "Registry: reason required");

        Proof storage p = proofs[_jobId][_milestoneIndex];
        require(p.status == VerificationStatus.Pending, "Registry: proof not pending");

        p.status          = VerificationStatus.Rejected;
        p.rejectionReason = _reason;
        totalRejected++;

        emit ProofRejected(_jobId, _milestoneIndex, p.submittedBy, _reason);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Read functions
    // ─────────────────────────────────────────────────────────────────────────

    function getProof(uint256 _jobId, uint256 _milestoneIndex)
        external
        view
        returns (Proof memory)
    {
        return proofs[_jobId][_milestoneIndex];
    }

    function getProofStatus(uint256 _jobId, uint256 _milestoneIndex)
        external
        view
        returns (VerificationStatus)
    {
        return proofs[_jobId][_milestoneIndex].status;
    }

    function getWorkerSubmissions(address _worker)
        external
        view
        returns (uint256[] memory)
    {
        return workerSubmissions[_worker];
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Admin
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Rotate the verifier wallet — use if backend key is compromised.
     *         Only callable by owner (your deployer wallet).
     */
    function setVerifier(address _newVerifier) external onlyOwner {
        require(_newVerifier != address(0), "Registry: invalid verifier");
        emit VerifierUpdated(verifier, _newVerifier);
        verifier = _newVerifier;
    }

    /**
     * @notice Update WorkChain address — use if contract is redeployed.
     */
    function setWorkChain(address _newWorkchain) external onlyOwner {
        require(_newWorkchain != address(0), "Registry: invalid address");
        emit WorkChainUpdated(address(workchain), _newWorkchain);
        workchain = IWorkChain(_newWorkchain);
    }

    /**
     * @notice Transfer ownership of this registry.
     */
    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "Registry: invalid owner");
        owner = _newOwner;
    }
}
