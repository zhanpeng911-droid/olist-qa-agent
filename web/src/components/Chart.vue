<template><div ref="element" :style="{height:height || '310px',width:'100%'}" role="img" :aria-label="label || '数据图表'"></div></template>
<script setup lang="ts">
import { ref,onMounted,onBeforeUnmount,watch,nextTick } from 'vue'
import * as echarts from 'echarts'
const props=defineProps<{option:any,height?:string,label?:string}>()
const element=ref<HTMLDivElement>(); let chart:echarts.ECharts|null=null; let observer:ResizeObserver; let frame=0
function render(){cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{if(!element.value||element.value.clientWidth<20)return; if(!chart)chart=echarts.init(element.value);chart.setOption(props.option,true);chart.resize()})}
onMounted(()=>{observer=new ResizeObserver(render);observer.observe(element.value!);nextTick(render)})
watch(()=>props.option,render,{deep:true})
onBeforeUnmount(()=>{cancelAnimationFrame(frame);observer?.disconnect();chart?.dispose()})
</script>
