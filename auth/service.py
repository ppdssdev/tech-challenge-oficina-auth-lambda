from auth.errors import AuthenticationFailed
from auth.ports import IdentityLookup, TokenSigner


class AuthenticationService:
    def __init__(self, identities: IdentityLookup, signer: TokenSigner):
        self.identities = identities
        self.signer = signer

    def authenticate(self, cpf):
        rows = self.identities.find(cpf)
        if len(rows) != 1 or rows[0].active is not True:
            raise AuthenticationFailed()
        return self.signer.sign(rows[0].customer_id)
