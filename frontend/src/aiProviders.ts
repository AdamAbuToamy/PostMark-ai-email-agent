export const aiProviders=['gemini','ollama','groq','openrouter','cerebras','mistral','zai','cloudflare','sambanova','openai','nvidia','siliconflow'] as const
export type Provider=typeof aiProviders[number]
export const providerNames:Record<Provider,string>={siliconflow:'SiliconFlow',nvidia:'NVIDIA NIM',gemini:'Gemini',ollama:'Ollama',groq:'Groq',openrouter:'OpenRouter',cerebras:'Cerebras',mistral:'Mistral',zai:'Z.AI',cloudflare:'Cloudflare Workers AI',sambanova:'SambaNova',openai:'OpenAI'}
export const providerKeySites:Partial<Record<Provider,string>>={gemini:'https://aistudio.google.com/api-keys',groq:'https://console.groq.com/keys',mistral:'https://console.mistral.ai/api-keys/',zai:'https://z.ai/manage-apikey/apikey-list'}

// Historical identifiers remain typed; only supported routes are offered for setup.
export const activeAIProviders:readonly Provider[]=['gemini','ollama','groq','mistral','zai']

export const setupAIProviders:readonly Provider[]=activeAIProviders
