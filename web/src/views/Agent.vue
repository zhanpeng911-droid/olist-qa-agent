<template>
  <div class="page">
    <p class="intro">LLM 按 skill 理解需求、检查表结构并动态生成 SQL。取数只读；工程 SQL 在隔离候选库中执行，通过质量门后由你确认发布。</p>
    <div v-if="error" class="error">{{error}}</div>
    <div v-if="connectionError" class="error">{{connectionError}}</div>
    <section class="panel">
      <div class="panel-title"><h2>创建 AI 任务</h2><small>{{settings?.configured ? `模型：${settings.model}` : '请在 .env 中配置 DeepSeek 后重启服务'}}</small></div>
      <div class="import-controls"><label>任务类型<select v-model="mode"><option value="query">自然语言取数</option><option value="engineering">自主数据工程</option></select></label><label v-if="mode==='engineering'" class="grow">已校验源批次<select v-model="batch"><option value="">选择待构建批次</option><option v-for="b in batches.filter(b=>b.status==='ready')" :key="b.id" :value="b.id">{{b.label}} · {{b.id}}</option></select></label></div>
      <textarea v-model="question" class="agent-question" aria-label="任务要求" :placeholder="mode==='query' ? '例如：按购买月份统计2018年上半年RJ州已送达订单的商品金额、订单量和低评分率，排除无评分订单的评分分母。' : '例如：合并所选批次，按Olist skill生成并执行四张Staging和三张Mart的SQL，完成质量校验，等待我确认发布。'"></textarea>
      <p class="footnote" v-if="mode==='engineering'">先在“数据工程”页上传／校验九表，不要点击固定流程的“构建并发布”；AI 会基于该批次建立候选库并编写建模SQL。输入不是任意行业数据，范围仍为Olist九表合约。</p>
      <div class="agent-consents"><label><input type="checkbox" v-model="consent">允许向模型发送本次指令、字段结构、skill、校验摘要及工具状态</label><label><input type="checkbox" v-model="shareResults">另允许发送查询结果预览（每次最多50行；默认关闭）</label></div>
      <div class="actions"><button :disabled="busy || !consent || !question.trim() || !settings?.configured || (mode==='engineering'&&!batch)" @click="start">{{busy?'正在提交…':'开始 AI 任务'}}</button></div>
      <p class="footnote">数据库密码及API Key不会提供给模型。轻量结果最多5,000行；可另行按原始SQL启动完整导出，数据不发给模型。完整导出超过200万行、1GiB或5分钟会失败，不提供部分CSV。</p>
    </section>
    <div class="two-col batch-layout"><section class="panel"><h2>任务记录</h2><div v-if="!tasks.length" class="empty">尚未创建任务。</div><button v-for="t in tasks" :key="t.id" :class="['batch-row',selected===t.id?'selected':'']" @click="selected=t.id"><div><b>{{t.label}}</b><small>{{t.mode==='query'?'取数':'工程'}} · {{new Date(t.created_at).toLocaleString()}}</small></div><span class="badge">{{labels[t.status] || t.status}}</span></button></section>
    <section v-if="current" class="panel"><div class="panel-title"><h2>{{current.label}}</h2><span class="badge">{{labels[current.status] || current.status}}</span></div><p class="muted">{{current.phase}}</p><p class="footnote" v-if="current.skill">skill: {{current.skill.name}} · 模型调用 {{current.rounds}} 次 · 工具调用 {{current.tool_count}} 次</p><p class="footnote" v-if="current.candidate">候选库：{{current.candidate}}</p><div v-if="current.error" class="error">{{current.error}}</div><p v-if="current.answer" class="agent-answer">{{current.answer}}</p>
      <div v-if="current.status==='needs_input'"><textarea v-model="answer" class="agent-question" aria-label="补充说明" placeholder="补充业务口径或澄清回答"></textarea><button :disabled="busy || !consent || !answer.trim()" @click="resume">补充并继续</button></div>
      <div v-if="current.status==='ready_for_publish'" class="actions"><button :disabled="busy" @click="publish">确认发布候选版本</button></div>
      <div v-if="current.status==='published'" class="actions"><button class="secondary" :disabled="busy" @click="previewRollback">预览恢复上一版</button></div>
      <div v-if="rollbackPlan && rollbackPlan.task===current.id" class="rollback-plan"><p>将恢复 {{rollbackPlan.backup}} 的完整三层数据；当前版本保留在 {{rollbackPlan.retired}}，不删除。</p><p class="footnote">备份质量检查无FAIL。只有当前发布版本可恢复；首次空库发布无完整前版时拒绝恢复。</p><button :disabled="busy" @click="restore">确认恢复上一版</button></div>
      <div v-if="current.sql_log?.length" class="actions"><a :href="`/api/agent/tasks/${current.id}/sql`">下载本任务生成SQL</a></div>
      <details v-if="current.events?.length"><summary>执行轨迹</summary><ol class="events"><li v-for="(e,i) in current.events" :key="i">{{e.phase}} · {{new Date(e.at).toLocaleTimeString()}}</li></ol></details>
    </section></div>
    <section v-for="result in current?.queries || []" :key="result.id" class="panel"><div class="panel-title"><h2>取数结果 · {{result.id}}</h2><a :href="`/api/agent/tasks/${current!.id}/results/${result.id}/csv`">下载轻量结果 CSV</a></div><p class="footnote">本地结果 {{number(result.row_count)}} 行，页面预览最多50行。{{result.truncated ? `已达到${result.row_limit}行上限，轻量CSV不完整；可启动完整导出。` : '未因工具上限截断。'}}{{current?.share_results ? '已允许向模型发送预览。' : '数据行未发送给模型。'}}</p><button v-if="result.scope!=='candidate'" class="secondary" :disabled="busy||current?.status==='running'||exports.some(e=>e.status==='running')" @click="exportFull(result.id)">完整导出原始 SQL 结果</button><p class="footnote">按当前已发布版本重新执行原始SQL；保留其中用户指定的LIMIT与筛选，旧预览可能属于较早版本。只有成功完成的文件可下载。</p><div v-for="exp in exports.filter(e=>e.result===result.id)" :key="exp.id" class="export-job"><span class="badge">{{labels[exp.status]||exp.status}}</span> {{number(exp.row_count)}} 行 <span v-if="exp.error" class="negative">{{exp.error}}</span><a v-if="exp.status==='completed'&&exp.complete" :href="`/api/agent/tasks/${current!.id}/exports/${exp.id}/csv`">下载完整 CSV（{{number(exp.row_count)}}行）</a></div><details><summary>查看原始 SQL（完整导出使用）</summary><pre class="sql-block">{{result.sql}}</pre></details><details><summary>查看轻量结果执行 SQL</summary><pre class="sql-block">{{result.executed_sql}}</pre></details><div class="table-wrap"><table><thead><tr><th v-for="c in result.columns" :key="c">{{c}}</th></tr></thead><tbody><tr v-for="(r,i) in result.rows" :key="i"><td v-for="c in result.columns" :key="c">{{r[c] ?? 'NULL'}}</td></tr></tbody></table></div></section>
    <section v-if="current?.sql_log?.length" class="panel"><h2>模型提交的工程 SQL</h2><details v-for="(s,i) in current.sql_log" :key="i"><summary>语句 {{i+1}} · {{s.status==='executed'?'已执行':'执行尝试（可能被拒绝）'}}</summary><pre class="sql-block">{{s.sql}}</pre></details></section>
    <section v-if="current?.checks?.length" class="panel"><h2>候选质量门</h2><div class="checks-grid"><div v-for="c in current.checks" :key="c.name" class="check"><span :class="['badge',c.status]">{{c.status}}</span><div><b>{{c.name}}</b><small>实际 {{c.actual}} / 预期 {{c.expected}}</small></div></div></div></section>
  </div>
</template>
<script setup lang="ts">
import {ref,computed,onMounted,onBeforeUnmount} from 'vue'
import {api,post,number,refreshMeta} from '../state'
import {askConfirmation} from '../confirmation'
const question=ref(''),mode=ref('query'),batch=ref(''),consent=ref(false),shareResults=ref(false),busy=ref(false),error=ref(''),connectionError=ref(''),tasks=ref<any[]>([]),batches=ref<any[]>([]),settings=ref<any>(),selected=ref(''),answer=ref(''),exports=ref<any[]>([]),rollbackPlan=ref<any>(null);let timer:any;let loading=false
const current=computed(()=>tasks.value.find(t=>t.id===selected.value))
const labels:any={running:'执行中',completed:'已完成',needs_input:'等待补充',failed:'失败',interrupted:'已中断',ready_for_publish:'待确认发布',published:'已发布',rolling_back:'恢复中',rolled_back:'已恢复上一版'}
async function load(){if(loading)return;loading=true;try{[tasks.value,batches.value,settings.value]=await Promise.all([api('/api/agent/tasks'),api('/api/imports'),api('/api/agent/settings')]);if(!selected.value&&tasks.value.length)selected.value=tasks.value[0].id;if(selected.value)exports.value=await api(`/api/agent/tasks/${selected.value}/exports`);connectionError.value=''}catch(e:any){connectionError.value=e.message}finally{loading=false}}
async function submit(fn:()=>Promise<any>){busy.value=true;error.value='';try{await fn();await load()}catch(e:any){error.value=e.message}finally{busy.value=false}}
async function start(){await submit(async()=>{const r=await post('/api/agent/tasks',{question:question.value,mode:mode.value,batch:mode.value==='engineering'?batch.value:null,consent:consent.value,share_results:shareResults.value});selected.value=r.id})}
async function resume(){await submit(async()=>{await post(`/api/agent/tasks/${selected.value}/continue`,{answer:answer.value,consent:consent.value});answer.value=''})}
async function publish(){if(!await askConfirmation('确认已查看模型SQL和质量门，并将候选版本原子发布到正式库？旧版本会保留为备份。'))return;await submit(async()=>{await post(`/api/agent/tasks/${selected.value}/publish`,{confirmed:true});await refreshMeta()})}
async function previewRollback(){await submit(async()=>{rollbackPlan.value=await api(`/api/agent/tasks/${selected.value}/rollback-plan`)})}
async function restore(){if(!rollbackPlan.value||!await askConfirmation('确认恢复完整上一版？当前版本将保留，不删除；此操作不调用模型。'))return;await submit(async()=>{await post(`/api/agent/tasks/${selected.value}/rollback`,{confirmed:true,token:rollbackPlan.value.token});rollbackPlan.value=null;await refreshMeta()})}
async function exportFull(result:string){if(!await askConfirmation('按当前已发布版本完整执行原始SQL并生成本地CSV？不发送数据给模型；超过资源预算会失败，不提供部分文件。'))return;await submit(()=>post(`/api/agent/tasks/${selected.value}/results/${result}/export-full`,{confirmed:true}))}
onMounted(()=>{load();timer=setInterval(load,3000)});onBeforeUnmount(()=>clearInterval(timer))
</script>
<style scoped>
.agent-question{display:block;width:100%;min-height:115px;resize:vertical;margin:20px 0;padding:14px;border:1px solid var(--border);border-radius:8px;font:inherit;font-size:13px;line-height:1.8;color:#243954}.agent-consents{display:grid;gap:12px;margin:20px 0}.agent-consents label{align-items:flex-start;line-height:1.8}.agent-consents input{margin-top:5px}.agent-answer{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9;font-size:13px}.sql-block{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;padding:16px;font-size:11px;line-height:1.8;border-radius:8px}.actions{margin-top:16px}
</style>
