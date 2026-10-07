<template><div class="filters"><label>购买月份<input aria-label="开始月份" type="month" v-model="state.filter.start"></label><span class="to">至</span><input aria-label="结束月份" type="month" v-model="state.filter.end"><label>客户州<select v-model="state.filter.state"><option value="">全部</option><option v-for="s in state.meta?.states" :key="s">{{s}}</option></select></label><button class="secondary" @click="reset">恢复默认</button><span class="muted">A / B 页共用筛选</span></div></template>
<script setup lang="ts">
import { state } from '../state'
function reset(){Object.assign(state.filter,{start:state.meta?.defaults.start || '',end:state.meta?.defaults.end || '',state:''})}
</script>
