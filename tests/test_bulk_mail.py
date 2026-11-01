"""Real temporary SQLite and mocked destructive transport only."""
from contextlib import nullcontext
import json
import sqlite3
import time
from unittest.mock import Mock
from googleapiclient.errors import HttpError
import httplib2
from test_batch3 import Base
from email_agent.domain.message import EmailMessage
from email_agent.application.bulk_mail import BulkMail
from email_agent.infrastructure.database.bulk_mail_store import SQLiteBulkMailStore
from email_agent.infrastructure.database.mail_store import SQLiteMailStore
from email_agent.application.mail_management import assess

class BulkMailTests(Base):
 def setUp(self):
  super().setUp()
  with sqlite3.connect(self.messages.database_path) as db:
   db.execute('DELETE FROM messages');db.execute('DELETE FROM message_category_assignments')
  self.bs=SQLiteBulkMailStore(self.messages.database_path,self.store);self.labels={};self.calls=[]
  def apply(op,id,labels):
   self.calls.append((op,id));self.labels[id]=tuple((set(labels)|{'TRASH'})-{'INBOX'}) if op=='trash' else tuple(set(labels)-{'TRASH'});return self.labels[id]
  self.remote=Mock();self.remote.labels.side_effect=lambda id:self.labels[id];self.remote.apply.side_effect=apply
  self.remote.delete.side_effect=lambda id:(self.calls.append(('delete',id)),self.labels.pop(id,None))
  self.remote.exists.side_effect=lambda id:id in self.labels
  self.allowed=True
  self.service=BulkMail(self.messages,self.assignments,self.bs,lambda account,op:self.remote,lambda account,op:self.allowed,lambda a,m,d,o,i:'Sensitive financial message' if d.get('category_id')=='bills' else None,lambda account:nullcontext())
  self.scope={'view':'category','category':'promotions','state':'all','search':''}
 def add_fixture(self,id,category='promotions',labels=('INBOX','CATEGORY_PROMOTIONS'),account='personal',sender='News <news@example.test>',headers=(('list-id','<weekly.example.test>'),)):
  m=EmailMessage(id,account,sender,'Special discount sale '+id,int(time.time()*1000),labels,headers);self.messages.save(m);self.messages.record_search_content(account,id,'A special sale discount shop now.',complete=True);self.classifier.classify(m)
  with sqlite3.connect(self.messages.database_path) as db:db.execute("UPDATE message_category_assignments SET category_id=?,status='classified',source='automatic' WHERE account_id=? AND message_id=?",(category,account,id))
  self.labels[id]=labels;return m
 def run_all(self,state,confirmation=''):
  self.bs.approve(state['account'],state['id'],True,confirmation)
  for _ in range(30):
   state=self.service.step(state['account'],state['id'])
   if state['status']!='running':return state
  self.fail('Operation did not finish within bounded fixture steps')
 def test_multiple_pages_exact_count_restore_and_no_duplicate_writes(self):
  for i in range(55):self.add_fixture(f'm{i:03}')
  state=self.service.preview('personal','trash',self.scope);self.assertEqual(state['count'],55);self.assertEqual(len(state['items']),50);self.assertEqual(len(self.bs.get('personal',state['id'],50)['items']),5)
  self.add_fixture('later');state=self.run_all(state);self.assertEqual(state['counts']['succeeded'],55);self.assertNotIn(('trash','later'),self.calls)
  before=len(self.calls);self.service.step('personal',state['id']);self.assertEqual(len(self.calls),before)
  restore=self.service.preview('personal','restore',{'view':'trash','search':''},['m000','m001']);self.run_all(restore);self.assertNotIn('TRASH',self.labels['m000'])
 def test_sender_subscription_and_semantic_group_exclude_financial_mail(self):
  self.add_fixture('ad');self.add_fixture('receipt','bills')
  scope={**self.scope,'category':None}
  for criterion in ('sender','subscription'):
   p=self.service.preview('personal','trash',scope,criterion=criterion,anchor='ad');self.assertEqual(p['matched'],2);self.assertEqual(p['count'],1);self.assertEqual(p['excluded'],1)
  with sqlite3.connect(self.messages.database_path) as db:
   db.execute('CREATE TABLE IF NOT EXISTS semantic_frames(account_id TEXT,message_id TEXT,payload TEXT,PRIMARY KEY(account_id,message_id))')
   frame=dict(understood=True,quality_verified=True,input_coverage={'body_complete':True},primary_intent='Advertising',message_type='promotion')
   for id in ('ad','receipt'):db.execute('INSERT OR REPLACE INTO semantic_frames VALUES(?,?,?)',('personal',id,json.dumps(frame)))
  p=self.service.preview('personal','trash',scope,criterion='semantic_type',anchor='ad');self.assertEqual(p['matched'],2);self.assertEqual(p['count'],1)
  p=self.service.preview('personal','trash',scope,criterion='group',anchor='ad');self.assertEqual(p['matched'],1)
 def test_wrong_account_filters_and_changed_membership_do_not_execute(self):
  self.add_fixture('mine');self.add_fixture('theirs',account='university')
  with self.assertRaises(ValueError):self.service.preview('personal','trash',self.scope,['theirs'])
  p=self.service.preview('personal','trash',self.scope,['mine'])
  with self.assertRaises(LookupError):self.bs.get('university',p['id'])
  with sqlite3.connect(self.messages.database_path) as db:db.execute("UPDATE message_category_assignments SET source='manual' WHERE message_id='mine'")
  p=self.run_all(p);self.assertEqual(p['counts']['skipped'],1);self.assertEqual(self.calls,[])
 def test_permanent_deletion_requires_exact_confirmation_and_explicit_capability(self):
  self.add_fixture('trash',labels=('TRASH',));p=self.service.preview('personal','delete',{'view':'trash','search':''},['trash'])
  with self.assertRaises(ValueError):self.bs.approve('personal',p['id'],True,'DELETE 2')
  self.allowed=False;self.bs.approve('personal',p['id'],True,'DELETE 1')
  with self.assertRaises(PermissionError):self.service.step('personal',p['id'])
  self.allowed=True;p=self.service.step('personal',p['id']);self.assertEqual(p['counts']['succeeded'],1);self.assertIsNone(self.messages.get('personal','trash'))
  self.assertEqual(self.calls,[('delete','trash')])
 def test_unknown_write_result_is_never_replayed_and_can_be_reconciled(self):
  self.add_fixture('a');self.add_fixture('b');original=self.remote.apply.side_effect
  def uncertain(op,id,labels):
   result=original(op,id,labels)
   if id=='a':raise TimeoutError('lost response')
   return result
  self.remote.apply.side_effect=uncertain;p=self.run_all(self.service.preview('personal','trash',self.scope))
  self.assertEqual(p['counts']['uncertain'],1);before=len(self.calls);self.service.step('personal',p['id']);self.assertEqual(len(self.calls),before)
  p=self.service.reconcile('personal',p['id']);self.assertEqual(p['counts']['succeeded'],2);self.assertEqual(len(self.calls),before)
 def test_read_429_cooldown_preserves_task_and_does_not_repeat_success(self):
  self.add_fixture('a');self.add_fixture('b');p=self.service.preview('personal','trash',self.scope);self.bs.approve('personal',p['id'],True,'')
  def labels(id):
   if id=='b':raise HttpError(httplib2.Response({'status':'429','retry-after':'120'}),b'{"error":{"message":"quota"}}')
   return self.labels[id]
  self.remote.labels.side_effect=labels;p=self.service.step('personal',p['id']);self.assertEqual(p['counts']['succeeded'],1);self.assertGreater(p['due'],time.time()+100);self.assertEqual(p['counts']['pending'],1)
  self.service.step('personal',p['id']);self.assertEqual(self.calls,[('trash','a')])
 def test_restart_executing_receipt_becomes_uncertain_not_replayed(self):
  self.add_fixture('a');p=self.service.preview('personal','trash',self.scope);self.bs.approve('personal',p['id'],True,'');self.bs.mark(p['id'],'a','executing',{})
  p=self.service.step('personal',p['id']);self.assertEqual(p['counts']['uncertain'],1);self.assertEqual(self.calls,[])
 def test_real_cleanup_policy_retains_receipts_security_and_manual_choices(self):
  from dataclasses import replace
  for subject,category in [('Paid invoice receipt','bills'),('Password changed','security'),('Interview invitation','job_applications')]:
   m=EmailMessage('x','personal','news@example.test',subject,label_ids=('TRASH',))
   result=assess(replace(m,label_ids=()),{'category_id':category,'status':'classified','source':'automatic'},[],None,subject)
   self.assertEqual(result['verdict'],'protected')

 def test_api_denies_foreign_client_false_confirmation_and_missing_delete_scope(self):
  from fastapi import FastAPI,HTTPException
  from starlette.requests import Request
  from email_agent.api.bulk_mail import register_bulk_mail,Selection,Scope,Confirmation
  from email_agent.application.gmail_actions import GmailActions
  from email_agent.application.promotional_cleanup import PromotionalCleanup
  from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
  self.add_fixture('trash',labels=('TRASH',))
  cleanup=PromotionalCleanup(self.messages,self.assignments,SQLiteCleanupStore(self.messages.database_path,self.root/'data/worker_state.db'),lambda _:self.remote,lambda _:True)
  gateway=Mock();gateway.capabilities.return_value={'gmail_modify':True,'gmail_permanent_delete':False}
  actions=GmailActions(self.messages,self.assignments,self.store,lambda _:self.remote,operation_lock=lambda _:nullcontext())
  app=FastAPI();register_bulk_mail(app,self.messages,gateway,cleanup,actions)
  endpoints={r.path:r.endpoint for r in app.routes if r.path.startswith('/api/bulk-mail')}
  req=Request({'type':'http','method':'POST','path':'/','client':('127.0.0.1',1),'headers':[(b'x-email-agent-action',b'message-management'),(b'origin',b'http://localhost:8000')]})
  payload=Selection(operation='delete',scope=Scope(view='trash'),ids=['trash'])
  preview=endpoints['/api/bulk-mail/{account}/preview']('personal',payload,req)
  self.assertEqual(preview['count'],1)
  with self.assertRaises(HTTPException) as missing:endpoints['/api/bulk-mail/{account}/{id}/confirm']('personal',preview['id'],Confirmation(approved=True,confirmation='DELETE 1'),req)
  self.assertEqual(missing.exception.status_code,403);self.assertEqual(self.calls,[])
  foreign=Request({'type':'http','method':'POST','path':'/','client':('192.0.2.1',1),'headers':[(b'x-email-agent-action',b'message-management')]})
  with self.assertRaises(HTTPException) as denied:endpoints['/api/bulk-mail/{account}/preview']('personal',payload,foreign)
  self.assertEqual(denied.exception.status_code,403)
