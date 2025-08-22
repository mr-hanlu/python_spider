## 安装
`pip install selenium`

安装浏览器驱动，选择浏览器对应版本：https://googlechromelabs.github.io/chrome-for-testing/

## 爬虫介绍
1. 爬取 `https://www.youlai.cn/yyk/hospindex/1/` 网站的医院和医生基本信息，保存为csv文件
   * crawl_progress.json文件是进度配置，医院序号，主科室索引，子科室索引
   * pending_doctors.json文件是当前医生页的缓存，逐个保存，防止意外中断，可以继续进度
   * hospitals_info.csv是医院信息表
   * hospital_doctors_data是医生信息表，一个医院一个csv文件
   * scraper.log是保存的日志文件