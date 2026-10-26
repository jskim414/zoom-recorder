"""A local named pipe can reveal the existing UI; it cannot start recording."""
import hashlib
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name(root: Path) -> str:
    return "zoom-recorder-" + hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]


def reveal_existing(root: Path) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name(root))
    connected = socket.waitForConnected(300)
    if connected:
        socket.disconnectFromServer()
    return connected


def listen_for_launch(root: Path, window):
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    # Caller already owns the recordings lock, so stale endpoints can be removed.
    QLocalServer.removeServer(server_name(root))
    if not server.listen(server_name(root)):
        raise RuntimeError("앱 호출 연결을 열 수 없습니다: " + server.errorString())
    def activate():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            window.reveal()
            socket.disconnectFromServer()
            socket.deleteLater()
    server.newConnection.connect(activate)
    return server
