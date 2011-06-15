
import base64
import random
import socket
import threading
import select
import sys
from Queue import Queue

from multiprocessing.connection import Listener

MAX_PACKET_ID = 8192

def encode(data):
    return base64.urlsafe_b64encode(data)

def decode(data):
    return base64.urlsafe_b64decode(data)

class Connection(object):
    def __init__(self, transport, addr, port):
        self.sock = None
        self.dest_host = (addr, port)
        self.transport = None

        self.write_lock = threading.Lock()
        self.read_lock = threading.Lock()

        self.open = False
        self.open_lock = threading.Lock()

        self.read_id = 0
        self.write_id = 0

        # Temporary holding place for out-of-order payloads
        self.payload_hold = dict()

        self.recv_queue = Queue()

        if transport == "udp":
            self.transport = socket.SOCK_DGRAM
        elif transport == "tcp":
            self.transport = socket.SOCK_STREAM
        else:
            raise Exception("Invalid transport type. Only tcp and udp are supported")

    def __io_loop(self):
        while True:
            data = self.sock.recv(4096)
            if data:
                self.recv_queue.put((self.read_id, data))
                self.read_id = (self.read_id + 1) % MAX_PACKET_ID;
            else:
                # Error condition
                self.recv_queue.put(None)
                break

    def __check_open(self):
        with self.open_lock:
            if not self.open:
                self.sock = socket.socket(socket.AF_INET, self.transport)
                self.sock.connect(self.dest_host)
                self.open = True

                t = threading.Thread(target=self.__io_loop)
                t.daemon = True
                t.start()

    def read(self):
        self.__check_open()
        return self.recv_queue.get()

    def write(self, payload_id, data):
        self.__check_open()
        with self.write_lock:
            try:
                if payload_id != self.write_id:
                    self.payload_hold[payload_id] = data
                else:
                    self.sock.send(data)

                    self.write_id = (self.write_id + 1) % MAX_PACKET_ID
                    while self.write_id in self.payload_hold:
                        payload = self.payload_hold.pop(self.write_id)
                        self.sock.send(payload)
                        self.write_id = (self.write_id + 1) % MAX_PACKET_ID

            except error:
                self.sock.close()

class ConnectionManager(object):
    def __init__(self, address, family):
        self.address = address
        self.family = family

        self.methods = dict()
        self.methods["open"] = {"arguments" : ("type", "addr", "port"),
                                "handler" : self._open}
        self.methods["read"] = {"arguments" : ("conn_id",),
                                "handler" : self._read}
        self.methods["write"] = {"arguments" : ("conn_id", "payload_id", "data"),
                                 "handler" : self._write}
        
        self.connections = dict()
                             
    def _error(self, message):
        return {"success" : False,
                "message" : message}

    def _success(self, **kwargs):
        kwargs["success"] = True
        return kwargs

    def _open(self, conn_type, addr, port):
        c_set = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        id_len = 32
        conn_id = ''.join([random.choice(c_set) for i in range(0, id_len)])
        self.connections[conn_id] = Connection(conn_type, addr, port)
        return self._success(conn_id = conn_id)

    def _read(self, conn_id):
        data = self.connections[conn_id].read()
        if data == None:
            return self._error("Connection closed")
        payload_id, payload = data
        message = "%d %s" % (payload_id, encode(payload))
        return self._success(data=message)

    def _write(self, conn_id, payload_id, data):
        self.connections[conn_id].write(payload_id, decode(data))
        return self._success()

    def _handle_conn(self, conn):
        while True:
            try:
                request = conn.recv()
            except IOError:
                return
            except EOFError:
                return

            if "action" not in request:
                return self._error("Malformed request")

            action = request["action"]
            if action not in self.methods:
                return self._error("Unsupported action")

            method = self.methods[action]
            arguments = []
            for arg in method["arguments"]:
                if arg in request:
                    arguments.append(request[arg])
                else:
                    return self._error("Missing argument to request %s" % (action,))

            response = method["handler"](*arguments)
            conn.send(response)

    def run(self):
        listener = Listener(self.address, self.family)

        while True:
            conn = listener.accept()
            t = threading.Thread(target=self._handle_conn, args=(conn,))
            t.daemon = True
            t.start()

if __name__ == "__main__":
    ConnectionManager(("127.0.0.1", 7655), 'AF_INET').run()
