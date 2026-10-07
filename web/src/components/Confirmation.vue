<template>
  <Teleport to="body">
    <dialog ref="dialog" aria-labelledby="operation-confirm-title" @cancel.prevent="finishConfirmation(false)">
      <h2 id="operation-confirm-title">确认操作</h2>
      <p>{{confirmationMessage}}</p>
      <div class="confirm-actions">
        <button class="secondary" autofocus @click="finishConfirmation(false)">取消</button>
        <button @click="finishConfirmation(true)">确认继续</button>
      </div>
    </dialog>
  </Teleport>
</template>
<script setup lang="ts">
import {ref,watch,nextTick,onBeforeUnmount} from 'vue'
import {confirmationMessage,finishConfirmation} from '../confirmation'
const dialog=ref<HTMLDialogElement>()
watch(confirmationMessage,async message=>{
  await nextTick()
  if(message&&!dialog.value?.open)dialog.value?.showModal()
  if(!message&&dialog.value?.open)dialog.value?.close()
})
onBeforeUnmount(()=>finishConfirmation(false))
</script>
<style scoped>
dialog{width:min(560px,calc(100vw - 40px));max-height:80vh;overflow:auto;border:1px solid #dfe6ef;border-radius:12px;padding:28px;color:#243954;background:white;box-shadow:0 12px 60px #14294a30}
dialog::backdrop{background:#14294a50}h2{margin:0 0 14px;font-size:20px}p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;font-size:14px}.confirm-actions{display:flex;justify-content:flex-end;gap:12px;margin-top:24px}
</style>
