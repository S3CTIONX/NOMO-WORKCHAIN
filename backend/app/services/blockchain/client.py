from web3 import Web3
from app.config.settings import RPC_URL

w3 = Web3(Web3.HTTPProvider(RPC_URL))

def release_payment(milestone_id):
    print(f"[BLOCKCHAIN] Executing on-chain release for milestone {milestone_id}")