"""Explicit, bounded full-result exports; independent from LLM context."""
import threading

from agent_core import database
from engineering.config import connect
from engineering.pipeline import scalar
from engineering.store import Store


class Exports:
    def __init__(self,root,agent):
        self.store=Store(root);self.agent=agent

    def start(self,task_id,result_id,confirmed=False):
        if not confirmed:raise ValueError('请确认按当前数据版本执行原始SQL并完整导出到本地')
        task=self.agent.store.get(task_id)
        if task['status'] in ('running','rolling_back'):raise ValueError('请等待当前任务完成')
        result=next((r for r in task.get('queries',[]) if r['id']==result_id),None)
        if not result:raise ValueError('结果不属于该任务')
        if result.get('scope','published')!='published':raise ValueError('完整导出仅支持已发布库，请先发布或重新取数')
        if not self.agent.jobs.lock.acquire(blocking=False):raise ValueError('数据任务或导出执行中，请稍后重试')
        try:
            row=self.store.create('', '完整查询结果导出')
            row=self.store.update(row['id'],task=task_id,result=result_id,database=self.agent.database,
                                  sql=result['sql'],status='running',phase='按当前版本导出',row_count=0,
                                  complete=False,truncated=False)
        except Exception:
            self.agent.jobs.lock.release();raise
        def worker():
            try:
                with connect() as control,control.cursor() as cur:
                    if not scalar(cur,"SELECT GET_LOCK('olist_engineering_publish',0)"):
                        raise ValueError('其他进程有数据构建或维护任务')
                    try:
                        token=database.version_token(control,row['database'])
                        self.store.update(row['id'],database_version=token)
                        output=database.full_csv(row['database'],row['sql'],self.store.root/row['id'],
                                                 lambda **kw:self.store.update(row['id'],**kw))
                        self.store.update(row['id'],status='completed',phase='完整导出完成',**output)
                    finally:cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")
            except Exception as error:
                detail=str(error) if isinstance(error,ValueError) else '完整导出失败（'+type(error).__name__+'），未提供部分文件'
                self.store.update(row['id'],status='failed',phase='完整导出失败',error=detail,complete=False)
            finally:self.agent.jobs.lock.release()
        threading.Thread(target=worker,daemon=True,name='olist-full-export').start()
        return row

    def list(self,task_id):
        self.agent.store.get(task_id)
        return [r for r in self.store.list() if r.get('task')==task_id]

    def artifact(self,task_id,export_id):
        row=self.store.get(export_id)
        if row.get('task')!=task_id or row['status']!='completed' or not row.get('complete'):
            raise ValueError('完整导出未成功或不属于该任务')
        path=self.store.root/export_id/'result.csv'
        if not path.is_file():raise ValueError('导出文件缺失，请重试')
        return path

    def recover(self):
        for row in self.store.list():
            if row['status']=='running':
                self.store.update(row['id'],status='interrupted',complete=False,phase='服务中断，请重新发起完整导出')
