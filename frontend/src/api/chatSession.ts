// A fresh token per browser document; private chat text never enters browser storage.
const session = crypto.randomUUID()
export const chatSessionHeaders = () => ({ 'X-Postmark-Chat-Session': session })
