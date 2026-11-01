import {useState} from 'react'
import type {AccountId} from '../api/emails'
import {postLocal} from '../api/actions'
import {useConfirmation} from './ConfirmAction'
export function MarkUnsubscribed({account,id,onChanged}:{account:AccountId;id:string;onChanged:()=>void}) {
 const {confirm,dialog}=useConfirmation();const [busy,setBusy]=useState(false),[error,setError]=useState('')
 async function done(){if(!await confirm('Confirm you completed this unsubscribe manually. This records your confirmation; it does not send a request or change Gmail.'))return;setBusy(true);setError('');try{await postLocal(`/api/subscriptions/${account}/${id}/manual-complete`,{approved:true,note:'User confirmed completing this unsubscribe manually.'});onChanged()}catch(e){setError(e instanceof Error?e.message:'Unable to save')}finally{setBusy(false)}}
 return <>{dialog}<button className="v5-small-button" disabled={busy} onClick={()=>void done()}>Mark as done</button>{error&&<p role="alert">{error}</p>}</>
}
