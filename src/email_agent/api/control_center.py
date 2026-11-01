"""Shared operational DTO for the full UI and native tray; local controls only."""
from fastapi import HTTPException,Request
import copy
import logging
import sqlite3
import threading
import time
from pydantic import BaseModel,ConfigDict
from typing import Literal
from email_agent.application.control_center import ControlCenter
from email_agent.infrastructure.database.control_center_store import SQLiteControlCenterStore
from email_agent.infrastructure.database.continuous_store import ContinuousStore
from email_agent.api.message_management import authorize_local_change


class SyncControl(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['resume','pause','retry']


def register_control_center(app,messages):
    work=ContinuousStore(messages.database_path,messages);work.initialize()
    service=ControlCenter(SQLiteControlCenterStore(work))
    snapshots={};engines={};locks={a:threading.Lock() for a in ('personal','university')}

    @app.get('/api/control/{account}')
    def overview(account:str,activity_only:bool=False):
        try:
            service.account(account)
            if activity_only:return service.store.activity(account,light=True)
            with locks[account]:
                saved=snapshots.get(account)
                if saved and time.time()-saved['observed_at']<5:return copy.deepcopy(saved)
                try:result=service.overview(account)
                except sqlite3.OperationalError:
                    if not saved:raise HTTPException(503,'Progress read delayed by SQLite; retry automatically')
                    result=copy.deepcopy(saved);result['stale']=True;return result
                result.update(observed_at=time.time(),stale=False,stale_sections=[])
                try:
                    state=app.state.ai_status_snapshot(account)
                    if not isinstance(state,dict) or not all(k in state for k in ('local','cloud','preference','free_only','organization_enabled','local_policy','work','providers')):raise RuntimeError('Invalid provider status contract')
                    engines[account]=copy.deepcopy(state)
                except Exception:
                    logging.getLogger(__name__).warning('Provider status read delayed; independent progress snapshot retained')
                    result['stale_sections'].append('engine')
                    state=engines.get(account,dict(local='Unknown',cloud='Unknown',preference='unknown',free_only=None,organization_enabled=None,local_policy={'enabled':None},work={},providers=[]))
                result['engine']={k:state.get(k) for k in ('local','cloud','preference','free_only','organization_enabled','local_policy','work')}
                try:result['activity']['active']=service.store.activity(account,light=True)['active']
                except Exception:
                    logging.getLogger(__name__).warning('Live activity read delayed; verified progress retained')
                    result['stale_sections'].append('activity')
                pending=result['progress']['total']-result['progress']['organized']
                result['activity']['state']='Processing' if result['activity']['active'] else 'Waiting for capacity' if pending and state['cloud']!='Available' and state['local'] not in ('Connected','Busy') else 'Queued' if pending else 'Idle'
                if not result['activity']['active'] and (state['organization_enabled'] is False or state['local_policy']['enabled'] is False):result['activity']['state']='Disabled'
                providers=state['providers']
                from email_agent.infrastructure.ai.resource_monitor import resources
                resource_state=resources.cached or {}
                result['engine']['local_throttled']=resource_state.get('local_throttled')
                result['engine']['local_resource_measured_at']=resource_state.get('measured_at')
                result['engine']['cloud_state']='Available' if state['cloud']=='Available' else 'Rate limited' if any(p['readiness']=='Available later' for p in providers if p['provider']!='ollama') else 'Awaiting authorization' if any(p['readiness']=='Awaiting account permission' for p in providers if p['configured']) else 'Unavailable'
                error=result['sync'].get('last_error') or ''
                result['account_connection']='Disconnected' if any(k in error.lower() for k in ('invalid_grant','authentication','unauthorized')) else 'Reconnecting' if any(k in error.lower() for k in ('network','connect','timeout','transport')) else 'Connected' if result['sync']['last_success'] else 'Unknown'
                snapshots[account]=copy.deepcopy(result)
                return result
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.post('/api/control/{account}/sync')
    def control(account:str,payload:SyncControl,request:Request):
        authorize_local_change(request)
        try:return service.sync(account,payload.action)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
