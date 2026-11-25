"""Almacén del JSON original de cada conversación. Carpeta local en el prototipo; S3 en AWS."""
import os
from pathlib import Path


class LocalRawStore:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def key(agent_code: str, conversation_id: str) -> str:
        # Misma forma que tendrá la clave de S3
        return f"raw/{agent_code}/{conversation_id}.json"

    def put(self, key: str, content: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)  # escritura atómica
