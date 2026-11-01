import { useRef, useState, useEffect } from 'react'

/** A scoped, keyboard-accessible confirmation; closing never grants consent. */
export function useConfirmation() {
 const [message,setMessage]=useState('')
 const resolve=useRef<((approved:boolean)=>void)|null>(null)
 const element=useRef<HTMLDialogElement>(null)
 const previousFocus=useRef<HTMLElement|null>(null)
 function finish(approved:boolean){element.current?.close();resolve.current?.(approved);resolve.current=null;setMessage('');previousFocus.current?.focus()}
 useEffect(()=>()=>{resolve.current?.(false)},[])
 useEffect(()=>{if(message)element.current?.showModal()},[message])
 function confirm(text:string){if(resolve.current)return Promise.resolve(false);previousFocus.current=document.activeElement as HTMLElement;setMessage(text);return new Promise<boolean>(done=>{resolve.current=done})}
 const dialog=<dialog ref={element} className="v5-confirm-dialog" aria-label="Confirm action" onCancel={e=>{e.preventDefault();finish(false)}}><h3>Confirm action</h3><p>{message}</p><div className="v5-preview-buttons"><button autoFocus className="v5-small-button" onClick={()=>finish(false)}>Cancel</button><button className="v5-small-button primary" onClick={()=>finish(true)}>Confirm</button></div></dialog>
 return {confirm,dialog}
}
