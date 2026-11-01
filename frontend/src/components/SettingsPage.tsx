import {useEffect,useState} from 'react'
import type {AccountId,EmailMessage} from '../api/emails'
import {readLocal} from '../api/mailboxes'
import {postLocal} from '../api/actions'
import {useConfirmation} from './ConfirmAction'
import {AccountSelect} from './AccountSelect'
import {HybridAISettings} from './HybridAISettings'
import {AutomaticCleanup} from './AutomaticCleanup'
function PrivacySettings({account}:{account:AccountId}){
 const {confirm,dialog}=useConfirmation();const [history,setHistory]=useState<boolean|null>(null),[busy,setBusy]=useState(false),[notice,setNotice]=useState('')
 useEffect(()=>{const c=new AbortController();readLocal<{save_history:boolean}>(`/api/chat/${account}/settings`,c.signal).then(value=>{if(!c.signal.aborted)setHistory(value.save_history)}).catch(()=>{if(!c.signal.aborted)setNotice('Privacy settings unavailable')});return()=>c.abort()},[account])
 async function save(value:boolean){if(!await confirm(value?'Save future chat history on this device?':'Stop saving chat history? This starts a new local conversation; Gmail is unchanged.'))return;setBusy(true);try{await postLocal(`/api/chat/${account}/history-preference`,{save_history:value});setHistory(value);setNotice('Privacy preference saved.')}catch(e){setNotice(e instanceof Error?e.message:'Unable to save')}finally{setBusy(false)}}
 async function clear(){if(!await confirm('Delete all local chat history for this account and cancel its pending chat approvals? Gmail is unchanged.'))return;setBusy(true);try{await postLocal(`/api/chat/${account}/conversations`,{operation:'clear_all',confirmed:true});setNotice('Local chat history deleted.')}catch(e){setNotice(e instanceof Error?e.message:'Unable to delete')}finally{setBusy(false)}}
 return <section className="pm-settings-section" aria-label="Privacy settings">{dialog}<h2>Privacy</h2><label className="v5-confirm-check"><input type="checkbox" checked={history??false} disabled={busy||history===null} onChange={e=>void save(e.target.checked)}/>Save chat history on this device</label><div className="v5-setting-row"><small>Email grouping stays local. AI needs separate consent.</small><button className="v5-small-button" disabled={busy} onClick={()=>void clear()}>Delete chat history</button></div>{notice&&<p role="status">{notice}</p>}</section>
}
export function SettingsPage({account,onAccountChange,onOpen,onChanged}:{account:AccountId;onAccountChange:(account:AccountId)=>void;onOpen:(message:EmailMessage)=>void;onChanged:()=>void}){
 const [diagnostics,setDiagnostics]=useState<unknown>(null)
 return <div className="pm-settings pm-control-settings"><section className="pm-settings-section" aria-label="Accounts settings"><div className="pm-settings-title"><h2>Account</h2><AccountSelect account={account} onChange={onAccountChange}/></div><a className="v5-small-button pm-history-link" href={`/sync-history?account=${account}`}>Sync &amp; History →</a></section><HybridAISettings key={`ai-${account}`} account={account}/><AutomaticCleanup key={`cleanup-${account}`} account={account} onAccountChange={onAccountChange} onOpen={onOpen} onChanged={onChanged} compact/><PrivacySettings key={`privacy-${account}`} account={account}/><details className="pm-settings-section pm-global-advanced" onToggle={e=>{if(e.currentTarget.open&&diagnostics===null)void readLocal(`/api/classifications/${account}/progress`).then(setDiagnostics).catch(()=>setDiagnostics('Diagnostics unavailable'))}}><summary>Advanced</summary><p>Local diagnostics for {account}. No Gmail or AI request.</p><pre>{diagnostics===null?'Loading…':JSON.stringify(diagnostics,null,2)}</pre></details></div>
}
