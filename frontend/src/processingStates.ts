export const processingLabel:Record<string,string>={waiting_body:'Waiting for email content',body_processing:'Reading email content',ready_local:'Queued for local AI',local_processing:'Local AI processing',waiting_cloud:'Waiting for cloud analysis',cloud_processing:'Cloud analysis',analyzed_other:'Analyzed · remains Other',failed:'Retry needed',blocked:'Needs content or attention',classified:'Classified'}

export const isModelAnalysis=(source?:string)=>['gemini','ollama','groq','openrouter','cerebras','mistral','openai'].includes(source??'')
