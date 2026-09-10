"""Erros do mcp-kicad.

Códigos no formato MK-<área>-<n> para que a mensagem devolvida ao agente diga
onde olhar sem precisar de stack trace.
"""


class McpKicadError(Exception):
    """Base. Todo erro exposto ao cliente MCP herda daqui."""

    code = "MK-GEN-000"

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()}"


class KicadCliNotFound(McpKicadError):
    code = "MK-CLI-001"


class KicadCliFailed(McpKicadError):
    code = "MK-CLI-002"

    def __init__(self, message: str, *, returncode: int, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class IpcUnavailable(McpKicadError):
    """Sessão do KiCad não encontrada, ou recurso que a versão não suporta."""

    code = "MK-IPC-001"


class UnsupportedByVersion(McpKicadError):
    """A operação existe na biblioteca mas não na versão do KiCad instalada."""

    code = "MK-IPC-002"


class ProfileNotFound(McpKicadError):
    code = "MK-FAB-001"


class RotationTableUnavailable(McpKicadError):
    """Tabela de correção de rotação (CPL) ausente do cache, ou download falhou."""

    code = "MK-FAB-002"


class RotationTableInvalid(McpKicadError):
    """Cache da tabela de correção de rotação existe mas está malformado/ilegível."""

    code = "MK-FAB-003"


class UnexpectedCliOutput(McpKicadError):
    """Saída do kicad-cli não tem a coluna/formato que o parsing espera.

    Levantado em vez de deixar KeyError/ValueError cru vazar do csv.DictReader
    para o cliente MCP — sinal de que uma versão futura do kicad-cli mudou
    nome de coluna ou formato de valor.
    """

    code = "MK-FAB-004"


class InvalidPath(McpKicadError):
    code = "MK-GEN-002"
