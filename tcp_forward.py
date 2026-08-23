import socket
import threading
import sys

def handle(client_sock):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_sock.connect(('127.0.0.1', 9222))
    except Exception as e:
        print(f"[Forwarder] Failed to connect to local Chrome on 9222: {e}")
        client_sock.close()
        return
    
    def forward(source, destination, rewrite_host=False, rewrite_response=False):
        import re
        try:
            while True:
                data = source.recv(8192)
                if not data:
                    break
                if rewrite_host and b"Host:" in data:
                    # Переписываем заголовок Host на localhost:9222, чтобы обойти защиту Chrome от DNS Rebinding
                    data = re.sub(br'(?i)Host:\s*[^\r\n]+', br'Host: localhost:9222', data)
                if rewrite_response:
                    # Заменяем локальный адрес дебаггера на адрес нашего форвардера, 
                    # чтобы Playwright из докера шел по websocket тоже через нас
                    old_len = len(data)
                    data = data.replace(b"localhost:9222", b"host.docker.internal:9223")
                    data = data.replace(b"127.0.0.1:9222", b"host.docker.internal:9223")
                    diff = len(data) - old_len
                    if diff != 0:
                        m = re.search(br'(?i)Content-Length:\s*(\d+)', data)
                        if m:
                            old_cl = int(m.group(1))
                            new_cl = old_cl + diff
                            data = data.replace(m.group(0), f"Content-Length: {new_cl}".encode('ascii'))
                destination.sendall(data)
        except Exception:
            pass
        finally:
            try:
                source.close()
            except Exception:
                pass
            try:
                destination.close()
            except Exception:
                pass
            
    t1 = threading.Thread(target=forward, args=(client_sock, server_sock, True, False), daemon=True)
    t2 = threading.Thread(target=forward, args=(server_sock, client_sock, False, True), daemon=True)
    t1.start()
    t2.start()

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    port = 9223
    try:
        s.bind(('0.0.0.0', port))
        s.listen(100)
        print(f"[Forwarder] Listening on 0.0.0.0:{port} -> 127.0.0.1:9222")
    except Exception as e:
        print(f"[Forwarder] Error binding to port {port}: {e}")
        sys.exit(1)
        
    while True:
        try:
            client, addr = s.accept()
            handle(client)
        except KeyboardInterrupt:
            print("[Forwarder] Shutting down...")
            break
        except Exception as e:
            print(f"[Forwarder] Error accepting connection: {e}")

if __name__ == '__main__':
    main()
