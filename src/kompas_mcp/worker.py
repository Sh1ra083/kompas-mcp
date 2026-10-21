"""Single process / single STA owner. Never retry a timed-out CAD write."""
import json
import logging
import multiprocessing as mp
import threading
import time
import uuid
from pathlib import Path
from .errors import CadError

def worker_main(connection, root: str):
    import pythoncom
    import win32event
    import win32api
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    from .backend import KompasBackend
    logging.basicConfig(filename=str(Path(root)/'worker.log'), level=logging.INFO)
    backend = KompasBackend(Path(root))
    mutex=win32event.CreateMutex(None,False,r'Local\KompasLocalMcpCadExecutionV1')
    try:
        while True:
            pythoncom.PumpWaitingMessages()
            if not connection.poll(0.025): continue
            command=connection.recv()
            if command is None: break
            started=time.time()
            acquired=False
            try:
                while not acquired:
                    wait=win32event.WaitForSingleObject(mutex,25)
                    acquired=wait in (win32event.WAIT_OBJECT_0,win32event.WAIT_ABANDONED)
                    pythoncom.PumpWaitingMessages()
                    if not acquired and time.time()-started>=110:
                        raise CadError('com_busy','Another MCP instance holds the CAD queue',outcome='not_started')
                data=backend.execute(command['action'], command['arguments'])
                result={'ok':True, 'data':data}
            except CadError as error:
                result=error.result()
            except pythoncom.com_error as error:
                hr=error.hresult & 0xffffffff
                code='com_busy' if hr in (0x80010001,0x8001010A) else 'com_error'
                if hr in (0x80010108,0x800706BA): code='connection_lost'
                result=CadError(code,str(error), hresult=hex(hr), outcome='inspect_document_before_retry').result()
                logging.exception('COM failure')
            except Exception as error:
                result=CadError('internal_error', str(error)).result()
                logging.exception('Worker failure')
            finally:
                if acquired:win32event.ReleaseMutex(mutex)
            result.update(operation_id=command['id'], elapsed_seconds=time.time()-started)
            with (Path(root)/'operations.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'action':command['action'],'arguments':command['arguments'], **result},ensure_ascii=False,default=str)+'\n')
            connection.send(result)
    finally:
        backend.release()
        win32api.CloseHandle(mutex)
        pythoncom.CoUninitialize()

class CadWorker:
    def __init__(self, root: Path, timeout: float=120):
        root.mkdir(parents=True,exist_ok=True)
        ctx=mp.get_context('spawn')
        self.connection, child=ctx.Pipe()
        self.process=ctx.Process(target=worker_main,args=(child,str(root)),daemon=True)
        self.process.start()
        child.close()
        self.lock=threading.Lock()
        self.pending=None
        self.timeout=timeout

    def call(self, action: str, arguments: dict, timeout: float|None=None):
        with self.lock:
            if self.pending:
                raise CadError('operation_pending','Resolve previous operation using operation_status',operation_id=self.pending)
            if not self.process.is_alive(): raise CadError('worker_lost','CAD worker is no longer running')
            token=uuid.uuid4().hex
            self.connection.send({'id':token,'action':action,'arguments':arguments})
            self.pending=token
            if not self.connection.poll(self.timeout if timeout is None else timeout):
                raise CadError('timeout_unknown_outcome','Operation may still be executing; do not retry',operation_id=token)
            result=self.connection.recv()
            self.pending=None
            return result

    def status(self):
        with self.lock:
            if self.pending and self.connection.poll():
                result=self.connection.recv(); self.pending=None
                return result
            return {'ok':True,'pending':self.pending,'worker_alive':self.process.is_alive()}

    def close(self):
        # Do not kill KOMPAS or a COM write in progress.
        if self.process.is_alive():
            try: self.connection.send(None)
            except (BrokenPipeError,OSError): pass
            self.process.join(2)
        self.connection.close()
