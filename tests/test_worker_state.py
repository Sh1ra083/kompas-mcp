import threading
import pytest
from kompas_mcp.worker import CadWorker
from kompas_mcp.errors import CadError

class Connection:
    def __init__(self):self.sent=[];self.ready=False
    def send(self,value):self.sent.append(value)
    def poll(self,*args):return self.ready
    def recv(self):return {'ok':True,'operation_id':self.sent[-1]['id']}
class Process:
    def is_alive(self):return True

def test_unknown_outcome_is_not_retried():
    w=object.__new__(CadWorker);w.connection=Connection();w.process=Process();w.lock=threading.Lock();w.pending=None;w.timeout=0
    with pytest.raises(CadError,match='may still be executing'):w.call('feature_create',{})
    with pytest.raises(CadError,match='previous operation'):w.call('feature_create',{})
    assert len(w.connection.sent)==1
    assert w.status()['pending']
    w.connection.ready=True
    assert w.status()['ok']
    assert w.pending is None
