#!/usr/bin/env python2

import argparse
import base64
import httplib
import urllib
import urlparse
import select
import socket
import sys
import threading
import time

MAX_PACKET_ID = 8192

def encode(data):
    return base64.urlsafe_b64encode(data)

def decode(data):
    return base64.urlsafe_b64decode(data)

def error(message, status=1):
    sys.stderr.write(message + "\n")
    sys.exit(status)

class SimpleHTTPRequest(object):
    def __init__(self, sock):
        self.sock = sock
        self.content_type = None
        self.transfer_encoding = None
        self.data_buffer = []
        self.out_buffer = []

    def __try_parse_headers(self):
        joined = ''.join(self.data_buffer)

        if '\n\n' in joined:
            headers, rest = joined.split('\n\n', 1)
        elif '\r\n\r\n' in joined:
            headers, rest = joined.split('\r\n\r\n', 1)
        else:
            return False

        self.data_buffer = []
        if rest:
            self.data_buffer.append(rest)

        for line in headers.split('\n'):
            if line.startswith("Content-Type: "):
                self.content_type = line.split(":", 1)[1].strip()
            elif line.startswith("Transfer-Encoding: "):
                self.transfer_encoding = line.split(":", 1)[1].strip()
                
    def __buffered_len(self):
        return sum(map(len, self.out_buffer))

    def __copy_buffers(self):
        if self.transfer_encoding != "chunked":
            self.out_buffer.extend(self.data_buffer)
            self.data_buffer = []
            return
        
        rest = ''.join(self.data_buffer)
        while '\r\n' in rest:
            chunk_size, other = rest.split('\r\n', 1)
            chunk_size = int(chunk_size.strip(), 16)

            if len(other) < chunk_size + 2:
                break

            self.out_buffer.append(other[:chunk_size])
            rest = other[chunk_size + 2:]
        self.data_buffer = [rest]

    def recv(self, at_least=1):
        while self.content_type == None or self.__buffered_len() < at_least:
            data = self.sock.recv(4096)

            if not data:
                continue
            
            self.data_buffer.append(data)

            if self.content_type == None:
                self.__try_parse_headers()
            
            if self.content_type != None:
                self.__copy_buffers()

        data = ''.join(self.out_buffer)
        self.out_buffer = []

        return data

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        self.sock.close()
                

class SimpleHTTPConnection(object):
    def __init__(self, host, port=80):
        self.host = host
        self.port = port
        self.headers = []

    def __add_header(self, name, value):
        self.headers.append("%s: %s" % (name, value))

    def __format_headers(self):
        return "\r\n".join(self.headers)

    def request(self, method, url, body=""):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.host, self.port))

        self.__add_header("Host", self.host)

        if body:
            self.__add_header("Content-Type", "application/x-www-form-urlencoded")
            self.__add_header("Content-Length", len(body))

        sock.send("%s %s HTTP/1.1\r\n%s\r\n\r\n%s" % (method, url, self.__format_headers(), body))

        return SimpleHTTPRequest(sock)

class TcpPipe(object):
    def __init__(self, proxy, remote_host, local_host):
        self.proxy = proxy
        self.remote_host = remote_host
        self.local_host = local_host

    def __io_loop(self, conn, conn_id):
        read_request = self.proxy.read_request(conn_id)
        read_buffer = ""
        out_packet_id = 0
        next_packet_id = 0

        # Hold packets which arrive out of order until they are ready to be used
        packet_hold = dict()
        
        while True:
            ready_read = select.select([conn, read_request], [], [])[0]

            if conn in ready_read:
                data = conn.recv(4096)
                if data:
                    message = "%d %s" % (out_packet_id, encode(data))
                    self.proxy.write_data(conn_id, message)
                    out_packet_id = (out_packet_id + 1) % MAX_PACKET_ID
                else:
                    break
            
            if read_request in ready_read:
                data = read_request.recv()
                read_buffer += data

                while True:
                    if read_buffer and not read_buffer.startswith("<"):
                        sys.stderr.write("Stream misaligned. Closing connection\n")
                        break

                    message_end = read_buffer.find(">")
                    if message_end > 0:
                        message = read_buffer[1:message_end]
                        packet_id, payload = message.split(" ", 1)
                        packet_id = int(packet_id)
                        #print payload
                        payload = decode(payload)
                        if packet_id == next_packet_id:
                            if payload:
                                conn.send(payload)

                            next_packet_id = (next_packet_id + 1) % MAX_PACKET_ID
                            while next_packet_id in packet_hold:
                                payload = packet_hold[next_packet_id]
                                del packet_hold[next_packet_id]
                                if payload:
                                    conn.send(payload)
                                next_packet_id = (next_packet_id + 1) % MAX_PACKET_ID
                        else:
                            packet_hold[packet_id] = payload

                        read_buffer = read_buffer[message_end + 1:]
                    else:
                        break

    def listen(self):
        self.local_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.local_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.local_sock.bind(self.local_host)
        self.local_sock.listen(128)

        while True:
            conn, addr = self.local_sock.accept()
            conn_id = self.proxy.open_pipe(self.remote_host[0], self.remote_host[1], "tcp")
            print "New connection", conn_id

            t = threading.Thread(target=self.__io_loop, args=(conn, conn_id))
            t.daemon = True
            t.start()

class ProxyClient(object):
    def __init__(self, proxy_uri):
        url_parts = urlparse.urlparse(proxy_uri)

        self.proxy_host = url_parts.netloc
        self.proxy_path = url_parts.path

    def __build_path(self, **params):
        return self.proxy_path + "?" + urllib.urlencode(params)

    def read_request(self, conn_id):
        http_conn = SimpleHTTPConnection(self.proxy_host)
        read_uri = self.__build_path(a="get", id=conn_id)
        return http_conn.request("GET", read_uri)

    def write_data(self, conn_id, data):
        http_conn = SimpleHTTPConnection(self.proxy_host)
        write_uri = self.__build_path(a="put", id=conn_id)
        data = urllib.urlencode({"data" : data})
        conn = http_conn.request("POST", write_uri, data)
        conn.close()

    def open_pipe(self, addr, port, transport="tcp"):
        http_conn = SimpleHTTPConnection(self.proxy_host)
        open_uri = self.__build_path(a="open", addr=addr, port=port, transport=transport)
        conn = http_conn.request("GET", open_uri)
        conn_id = conn.recv(32).strip()
        conn.close()
        return conn_id

ap = argparse.ArgumentParser(description="HTTP/CGI Proxy Client")
ap.add_argument("--proxy-host", dest="proxy_uri", help="Use alternate proxy URI", default="http://dauphin.brewtab.com/~christopher/proxy.cgi")
ap.add_argument("-F", dest="local_port", help="Forward traffic from local port", type=int, default=None)
ap.add_argument("host", help="Remote host")
ap.add_argument("port", type=int, help="Remote port")

args = ap.parse_args(sys.argv[1:])
if args.local_port == None:
    args.local_port = args.port

tunnel = TcpPipe(ProxyClient(args.proxy_uri), (args.host, args.port), ("127.0.0.1", args.local_port))
tunnel.listen()
