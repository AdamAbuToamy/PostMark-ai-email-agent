import type {AccountId} from './emails'
import {readLocal} from './mailboxes'
export interface SyncStatus {enabled:boolean;state:string;running:boolean;stage:string;inventory_complete:boolean;discovered:number;bodies_available:number;bodies_remaining:number;last_success:string|null;failures:number;retries:number;retryable:number;note:string;diagnostic:string|null}
export interface ControlStatus {account:AccountId;sync:SyncStatus;new_mail:number;awaiting_analysis:number;cleanup_candidates:number;latest_classification:{updated_at:string;source:string;model:string|null}|null}
export const getControl=(account:AccountId,signal?:AbortSignal)=>readLocal<ControlStatus>(`/api/control/${account}`,signal)
