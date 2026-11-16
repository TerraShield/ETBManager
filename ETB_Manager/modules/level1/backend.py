"""L'interphone consomme les données partagées par le lecteur mémoire central."""

class IntercomBackend:
    def snapshot(self, shared_state):
        return {"active": bool((shared_state.get("car_puzzle") or {}).get("active"))}


def create_module(context):
    return IntercomBackend()
