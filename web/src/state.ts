import { reactive } from 'vue'

export const state = reactive({ meta:null as any, error:'', filter:{start:'',end:'',state:''}, loading:false })
export async function api(path:string, options:RequestInit={}) {
  let result:Response
  try {result=await fetch(path,options)} catch {throw new Error('暂时无法连接应用服务，请检查服务后重试。')}
  if (!result.ok) {
    let message = `请求失败（${result.status}）`
    try { message=(await result.json()).detail || message } catch {}
    throw new Error(message)
  }
  return result.json()
}
export const post=(path:string,body:any={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
export async function refreshMeta() {
  state.loading=true
  try {
    const previous=state.meta?.defaults
    state.meta=await api('/api/meta'); state.error=''
    for (const key of ['start','end'] as const) {
      if (!state.filter[key] || state.filter[key]===previous?.[key]) state.filter[key]=state.meta.defaults[key] || ''
    }
  } catch(e:any){state.error=e.message}
  finally {state.loading=false}
}
export function query(extra:any={}) {
  return new URLSearchParams(Object.entries({...state.filter,...extra}).filter(([,v])=>v!=='' && v!=null).map(([k,v])=>[k,String(v)])).toString()
}
export function number(value:any,digits=0) {return value===null||value===undefined ? '—' : Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits})}
export function pct(value:any,digits=1) {return value===null||value===undefined ? '—' : `${(Number(value)*100).toFixed(digits)}%`}
export const metricLabels:any={product_value:'商品金额',delivered_orders:'已送达订单数',customers:'已送达客户数',aov:'商品客单价',orders_per_customer:'人均订单数',items:'商品件数',items_per_order:'单均件数',price_per_item:'件均价格'}
