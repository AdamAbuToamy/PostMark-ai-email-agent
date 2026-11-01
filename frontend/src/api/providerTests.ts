import {LocalActionError} from './actions'
export interface ProbeResult {passed:boolean;compatibility_passed?:boolean;status:string;provider?:string;model?:string;request_id?:string;requests?:number;inference_completed?:boolean;structured_valid?:boolean;semantic_correct?:boolean;quality_qualified?:boolean;reason?:string|null;diagnostic?:{kind?:string;reason?:string;http_status?:number;context?:Record<string,unknown>};issues?:string[];local_http?:number}
export function probeFailure(error:unknown):ProbeResult{
 return {passed:false,status:'blocked',requests:0,reason:'postmark_request_failed',local_http:error instanceof LocalActionError?error.status:undefined,diagnostic:{reason:error instanceof Error?error.message:'Connection to Postmark failed. The test was not retried.'}}
}
