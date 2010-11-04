#!/usr/bin/env python
"""A simple engine that talks to a controller over 0MQ.
it handles registration, etc. and launches a kernel
connected to the Controller's queue(s).
"""
from __future__ import print_function
import sys
import time
import traceback
import uuid
from pprint import pprint

import zmq
from zmq.eventloop import ioloop, zmqstream

from streamsession import Message, StreamSession
from client import Client
import streamkernel as kernel
import heartmonitor
from entry_point import make_base_argument_parser, connect_logger, parse_url
# import taskthread
# from log import logger


def printer(*msg):
    pprint(msg)

class Engine(object):
    """IPython engine"""
    
    id=None
    context=None
    loop=None
    session=None
    ident=None
    registrar=None
    heart=None
    kernel=None
    
    def __init__(self, context, loop, session, registrar, client, ident=None, heart_id=None):
        self.context = context
        self.loop = loop
        self.session = session
        self.registrar = registrar
        self.client = client
        self.ident = ident if ident else str(uuid.uuid4())
        self.registrar.on_send(printer)
        
    def register(self):
        
        content = dict(queue=self.ident, heartbeat=self.ident, control=self.ident)
        self.registrar.on_recv(self.complete_registration)
        self.session.send(self.registrar, "registration_request",content=content)
    
    def complete_registration(self, msg):
        # print msg
        idents,msg = self.session.feed_identities(msg)
        msg = Message(self.session.unpack_message(msg))
        if msg.content.status == 'ok':
            self.session.username = str(msg.content.id)
            queue_addr = msg.content.queue
            if queue_addr:
                queue = self.context.socket(zmq.PAIR)
                queue.setsockopt(zmq.IDENTITY, self.ident)
                queue.connect(str(queue_addr))
                self.queue = zmqstream.ZMQStream(queue, self.loop)
            
            control_addr = msg.content.control
            if control_addr:
                control = self.context.socket(zmq.PAIR)
                control.setsockopt(zmq.IDENTITY, self.ident)
                control.connect(str(control_addr))
                self.control = zmqstream.ZMQStream(control, self.loop)
            
            task_addr = msg.content.task
            print (task_addr)
            if task_addr:
                # task as stream:
                task = self.context.socket(zmq.PAIR)
                task.setsockopt(zmq.IDENTITY, self.ident)
                task.connect(str(task_addr))
                self.task_stream = zmqstream.ZMQStream(task, self.loop)
                # TaskThread:
                # mon_addr = msg.content.monitor
                # task = taskthread.TaskThread(zmq.PAIR, zmq.PUB, self.ident)
                # task.connect_in(str(task_addr))
                # task.connect_out(str(mon_addr))
                # self.task_stream = taskthread.QueueStream(*task.queues)
                # task.start()
            
            hbs = msg.content.heartbeat
            self.heart = heartmonitor.Heart(*map(str, hbs), heart_id=self.ident)
            self.heart.start()
            # ioloop.DelayedCallback(self.heart.start, 1000, self.loop).start()
            # placeholder for now:
            pub = self.context.socket(zmq.PUB)
            pub = zmqstream.ZMQStream(pub, self.loop)
            # create and start the kernel
            self.kernel = kernel.Kernel(self.session, self.control, self.queue, pub, self.task_stream, self.client)
            self.kernel.start()
        else:
            # logger.error("Registration Failed: %s"%msg)
            raise Exception("Registration Failed: %s"%msg)
        
        # logger.info("engine::completed registration with id %s"%self.session.username)
        
        print (msg)
    
    def unregister(self):
        self.session.send(self.registrar, "unregistration_request", content=dict(id=int(self.session.username)))
        time.sleep(1)
        sys.exit(0)
    
    def start(self):
        print ("registering")
        self.register()
        

def main():
    
    parser = make_base_argument_parser()
    
    args = parser.parse_args()
    
    parse_url(args)
    
    iface="%s://%s"%(args.transport,args.ip)+':%i'
    
    loop = ioloop.IOLoop.instance()
    session = StreamSession()
    ctx = zmq.Context()

    # setup logging
    connect_logger(ctx, iface%args.logport, root="engine", loglevel=args.loglevel)
    
    reg_conn = iface % args.regport
    print (reg_conn)
    print ("Starting the engine...", file=sys.__stderr__)
    
    reg = ctx.socket(zmq.PAIR)
    reg.connect(reg_conn)
    reg = zmqstream.ZMQStream(reg, loop)
    client = Client(reg_conn)
    
    e = Engine(ctx, loop, session, reg, client, args.ident)
    dc = ioloop.DelayedCallback(e.start, 100, loop)
    dc.start()
    loop.start()