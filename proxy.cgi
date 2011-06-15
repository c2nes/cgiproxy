#!/usr/bin/env python2.7

import cgi
import cgitb
cgitb.enable()

from httplib import *
from multiprocessing.connection import Client, Listener
import os
import sys
import re
import socket
import time

from connection_manager import ConnectionManager

SOCK = ("127.0.0.1", 7655)
form = cgi.FieldStorage()

def error():
    print "Content-Type: text/html"
    print
    print "Error"
    sys.exit(0)

def new_conn_form(err=None):
    print "Content-Type: text/html"
    print
    if err:
        print """<span style="color: red; font-style: italic;">%s</span>""" % (err,)
    print """
<table border="0">
<form action="" method="get">
<tr><td>Address:</td><td><input type="text" value="127.0.0.1" name="addr" /></td></tr>
<tr><td>Port:</td><td><input type="text" value="8022" name="port" /></td></tr>
<tr><td></td><td><input type="submit" /></td></tr>
</form>
</table>
"""
    sys.exit(0)

def open_connection(addr, port):
    c = Client(SOCK, 'AF_INET')
    c.send({"action": "open",
            "type": "tcp",
            "addr": addr,
            "port": port})
    response = c.recv()
    if response["success"]:
        return response["conn_id"]
    else:
        return None

action = "open"
if "a" in form:
    action = form["a"].value

if action not in ("put", "get", "open", "spawn"):
    error()

if action == "spawn":
    if os.fork() == 0:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdin.close()

        try:
            ConnectionManager(SOCK, 'AF_INET').run()
        except Exception:
            sys.exit(0)

    print "Content-Type: text/html"
    print
    print "Spawning..."
    sys.exit(0)

if action == "open":
    if "addr" not in form or "port" not in form:
        new_conn_form()

    addr = form["addr"].value
    port = form["port"].value

    try:
        addr = socket.gethostbyname(addr)
        port = int(port)
    except Exception:
        new_conn_form("Invalid host/port or could not resolve host")

    conn_id = open_connection(addr, port)
    
    print "Content-Type: text/html"
    print
    print conn_id
    sys.exit(0)

elif "id" not in form:
    error()

conn_id = form["id"].value
if action == "get":
    c = Client(SOCK, 'AF_UNIX')

    print "Content-Type: text/html"
    print

    while True:
        c.send({"action": "read",
                "conn_id" : conn_id})
        response = c.recv()

        if response["success"]:
            sys.stdout.write("<" + response["data"] + ">")
            sys.stdout.flush()
        else:
            sys.exit(1)

elif action == "put" and "data" in form:
    data = form["data"].value
    payload_id, payload = data.split(" ", 1)
    payload_id = int(payload_id)
    c = Client(SOCK, 'AF_UNIX')
    c.send({"action": "write",
            "conn_id": conn_id,
            "payload_id": payload_id,
            "data": payload})
