"""Tab-scoped in-memory chat storage; no temporary conversation content on disk."""
import re
import threading
import time
from email_agent.infrastructure.database.action_store import SQLiteActionStore


class TemporaryChatStore(SQLiteActionStore):
    def __init__(self, durable):
        self.durable=durable
        super().__init__(None,memory=True)

    def preferences(self,account): return self.durable.preferences(account)

    def finish(self,account,id,status,result):
        super().finish(account,id,status,result)
        self.durable.archive_remote(self.get(account,id),self.audit(account,id))

    def claim(self,account,id):
        claimed=super().claim(account,id)
        if claimed: self.durable.archive_remote(self.get(account,id),self.audit(account,id))
        return claimed

    def event(self,id,event,detail):
        super().event(id,event,detail)
        # Durable start evidence survives a process loss during a confirmed Gmail write.
        for account in ("personal","university"):
            try: value=self.get(account,id)
            except LookupError: continue
            self.durable.archive_remote(value,self.audit(account,id))
            break


class ChatSessionPool:
    TTL=1800
    MAX_SESSIONS=64

    def __init__(self,durable):
        self.durable=durable
        self.entries={}
        self.lock=threading.RLock()
        self.timer=None

    def get(self,account,token):
        if not isinstance(token,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}",token):
            raise ValueError("Temporary chat requires this tab's session identifier")
        with self.lock:
            if self.timer is None: self._schedule()
            now=time.monotonic()
            for key,(seen,store) in list(self.entries.items()):
                if now-seen>self.TTL:
                    store.close();del self.entries[key]
            key=(account,token)
            if key not in self.entries:
                if len(self.entries)>=self.MAX_SESSIONS: raise ValueError("Temporary session limit reached; close inactive chats")
                self.entries[key]=(now,TemporaryChatStore(self.durable))
            _,store=self.entries[key]
            self.entries[key]=(now,store)
            return store

    def end(self,account,token):
        with self.lock:
            entry=self.entries.get((account,token))
            if entry:
                entry[1].change_conversation(account,"clear_all")
                entry[1].close();del self.entries[(account,token)]
        return {"ended":True}

    def _schedule(self):
        self.timer=threading.Timer(60,self._expire)
        self.timer.daemon=True
        self.timer.start()

    def _expire(self):
        with self.lock:
            now=time.monotonic()
            for key,(seen,store) in list(self.entries.items()):
                if now-seen>self.TTL:
                    store.close();del self.entries[key]
            self.timer=None
            if self.entries:self._schedule()

    def close(self):
        with self.lock:
            if self.timer:self.timer.cancel();self.timer=None
            for _,store in self.entries.values():store.close()
            self.entries.clear()
