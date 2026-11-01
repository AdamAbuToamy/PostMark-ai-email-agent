import {useState} from 'react'
import type {AccountId} from '../api/emails'
import {postLocal} from '../api/actions'
export function ManualUnsubscribeButton({account,id,actionId,label='Open unsubscribe page myself',onOpened,primary=false}:{account:AccountId;id:string;actionId?:string;label?:string;onOpened?:()=>void;primary?:boolean}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[fallback,setFallback]=useState('')
 async function open(){
  const popup=window.open('about:blank','_blank')
  if(popup){popup.opener=null;popup.document.body.textContent='Checking unsubscribe page…'}
  setBusy(true);setError('')
  try{
   const result=await postLocal<{url:string}>(`/api/subscriptions/${account}/${id}/manual-page`,{action_id:actionId??null})
   const url=new URL(result.url)
   if(url.protocol!=='https:'||url.username||url.password)throw new Error('No safe unsubscribe page is available')
   if(popup&&!popup.closed)popup.location.replace(url.href);else setFallback(url.href)
   onOpened?.()
  }catch(e){popup?.close();setError(e instanceof Error?e.message:'Unable to open unsubscribe page')}
  finally{setBusy(false)}
 }
 return <><button className={`v5-small-button${primary?' primary':''}`} disabled={busy} onClick={()=>void open()}>{busy?'Opening…':label}</button>{fallback&&<a href={fallback} target="_blank" rel="noopener noreferrer">Open page manually</a>}{error&&<p role="alert">{error}</p>}</>
}
