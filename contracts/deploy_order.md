# Deployment Order — Critical

These two contracts are interdependent. Follow this exact order.

## Step 1 — Deploy WorkChain
```bash
npx hardhat run scripts/deploy.js --network monad
# Save: WORKCHAIN_ADDRESS=0x...
```
WorkChain deploys with no registry set (authorizedRegistry = address(0)).
Manual employer releases still work at this point.

## Step 2 — Deploy VerificationRegistry
```bash
# In deploy script, pass:
#   constructor(_verifier, _workchain)
#   _verifier  = your backend server wallet address
#   _workchain = WORKCHAIN_ADDRESS from Step 1
npx hardhat run scripts/deploy_registry.js --network monad
# Save: REGISTRY_ADDRESS=0x...
```

## Step 3 — Connect them
```bash
# Call WorkChain.setRegistry(REGISTRY_ADDRESS)
npx hardhat run scripts/set_registry.js --network monad
```

After Step 3:
- VerificationRegistry can call releaseMilestoneFromRegistry()
- WorkChain trusts the registry for auto-releases
- Employer can still call releaseMilestone() directly at any time

## If you redeploy either contract
- Redeploy WorkChain → redeploy Registry (needs new workchain address) → setRegistry()
- Redeploy Registry only → call setRegistry() with new address
- Never call setRegistry() with address(0) — it will revert
