import {ref} from 'vue'

export const confirmationMessage=ref('')
let pending:((confirmed:boolean)=>void)|undefined

export function askConfirmation(message:string):Promise<boolean>{
  if(pending)return Promise.resolve(false)
  confirmationMessage.value=message
  return new Promise(resolve=>{pending=resolve})
}

export function finishConfirmation(confirmed:boolean){
  const resolve=pending
  pending=undefined
  confirmationMessage.value=''
  resolve?.(confirmed)
}
