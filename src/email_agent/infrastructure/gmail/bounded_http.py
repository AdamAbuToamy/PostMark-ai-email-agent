"""Count actual HTTP connection/request attempts, including httplib2 replays."""
import httplib2


class BudgetConnection:
    def __init__(self,connection,reserve):
        self.connection,self.reserve,self.connected_reservation=connection,reserve,False
    def __getattr__(self,key):return getattr(self.connection,key)
    def connect(self):
        self.reserve();self.connected_reservation=True
        return self.connection.connect()
    def request(self,*args,**kwargs):
        if not self.connected_reservation:self.reserve()
        try:return self.connection.request(*args,**kwargs)
        finally:self.connected_reservation=False
    def close(self):
        self.connected_reservation=False
        return self.connection.close()


class BudgetHTTP(httplib2.Http):
    def __init__(self,reserve,timeout=30):
        super().__init__(timeout=timeout)
        self.follow_redirects=False
        self.reserve=reserve
    def _conn_request(self,connection,*args,**kwargs):
        return super()._conn_request(BudgetConnection(connection,self.reserve),*args,**kwargs)
