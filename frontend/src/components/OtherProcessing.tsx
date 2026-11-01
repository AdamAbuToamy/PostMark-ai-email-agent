export interface OtherProgress {scope:string;total:number;counts:Record<string,number>;moved_24h:number;last_activity:string|null}
export function OtherProcessing({progress}:{progress:OtherProgress}){
 const c=progress.counts,number=(...states:string[])=>states.reduce((sum,key)=>sum+(c[key]??0),0)
 const stats=[['Waiting for email content',number('waiting_body','body_processing')],['Local AI queued / processing',number('ready_local','local_processing')],['Waiting for cloud analysis',number('waiting_cloud','cloud_processing')],['Analyzed but still Other',number('analyzed_other','classified')],['Errors / blocked',number('failed','blocked')]] as const
 return <aside className="pm-other-progress" aria-label="Other AI organization"><h3>Other — AI organization</h3><small>All {progress.total} messages in Other · counts include New and Done</small><div>{stats.map(([label,n])=><span key={label}><strong>{n}</strong><small>{label}</small></span>)}<span><strong>{progress.moved_24h}</strong><small>Moved out · last 24h</small></span></div>{progress.last_activity&&<small>Last worker activity: {new Date(progress.last_activity.replace(' ','T')+'Z').toLocaleString()}</small>}</aside>
}
