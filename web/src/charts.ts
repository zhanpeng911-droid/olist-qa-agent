import {number,pct} from './state'
const colors=['#2463eb','#06a393','#f59e0b','#f15b66']
export function line(rows:any[],keys:string[],labels:string[],percent=false,decimals=0,zero=true){return {
 color:colors,tooltip:{trigger:'axis',valueFormatter:(v:any)=>percent?pct(v):number(v,1)},
 legend:{bottom:0},grid:{left:65,right:25,top:25,bottom:60},
 xAxis:{type:'category',data:rows.map(r=>r.month),axisLabel:{color:'#66758d'},axisLine:{lineStyle:{color:'#dae2ed'}}},
 yAxis:{type:'value',min:zero?0:undefined,scale:!zero,axisLabel:{formatter:(v:number)=>percent?pct(v,0):number(v,decimals)},splitLine:{lineStyle:{color:'#edf1f7'}}},
 series:keys.map((key,i)=>({name:labels[i],type:'line',smooth:false,connectNulls:false,showSymbol:rows.length<6,data:rows.map(r=>r[key]??null),lineStyle:{width:2.5}}))
}}
export function bars(rows:any[]){return {
 color:[colors[0]],tooltip:{trigger:'axis',axisPointer:{type:'shadow'},formatter:(params:any[])=>{const r=rows[params[0].dataIndex];return `${r.name}<br>商品金额 R$ ${number(r.product_value,2)}<br>金额贡献 ${pct(r.amount_share)}<br>订单数 ${number(r.orders)}`}},
 grid:{left:155,right:65,top:10,bottom:40},xAxis:{type:'value',axisLabel:{formatter:(v:number)=>v>=1000?`${number(v/1000)}K`:number(v)},splitLine:{lineStyle:{color:'#edf1f7'}}},
 yAxis:{type:'category',inverse:true,data:rows.map(r=>r.name),axisLabel:{width:140,overflow:'truncate'},axisLine:{show:false},axisTick:{show:false}},
 series:[{type:'bar',data:rows.map(r=>r.product_value),barMaxWidth:21,itemStyle:{borderRadius:[0,4,4,0]}}]
}}
