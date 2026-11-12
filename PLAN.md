# QuantLens 大数据主线改造计划

版本：v1
适用范围：本仓库的数据层、计算层、服务层、前端与课程演示材料
约束来源：`AGENTS.md`

---

## 0. 计划边界与硬约束

### 0.1 目标

把项目从“单机数据 + 孤立 Spark 模型”改成一条以因子研究为主线的分层大数据系统。课程要求的每个组件都落在真实数据流上，没有演示用孤岛。

主线固定为：

```
数据接入 → 因子构造与检验 → 因子合成 → 组合构建
        → 持仓建议 → 回测与风控 → 归因 → 回到因子
```

### 0.2 非目标

- 不做实时流计算，Spark Streaming / Structured Streaming 不进入主线。
- 不做多市场、多资产、盘中交易。
- 不保留独立的“预测模型”产品页、独立模型评估口径和独立模型跟踪调度。
- 不把逐日回测和账户记账改成分布式。
- 不为了凑课程章节增加没有产品落点的算法。

### 0.3 硬约束

- 不创建源码、配置或工作区备份；不生成 `.bak`、备份压缩包、恢复副本或文件快照。
- 所有修改在工作区原地进行；测试不得调用备份创建流程。
- 行情快照、研究证据、账户记录、事务恢复日志属于业务数据，不按备份清理。
- 迁移采用“新旧并行 + 读切换 + 保留旧数据”，不用复制工作区的方式做回退。
- 对外文字使用本地 `shuorenhua` skill；代码、日志、配置、命令输出不套用。

### 0.4 不换的部分

| 模块 | 保留理由 |
| --- | --- |
| `src/backtest/` 逐日撮合引擎 | 成交顺序、T+1、涨跌停、停牌、资金约束必须可复现，Spark 的并行无状态模型会破坏顺序 |
| `src/portfolio/` SQLite 账户与交易 | OLTP 事务，要求一致性；分析走 Hive/Spark，两者分工 |
| `src/market_rules/` | 交易规则与费用口径，回测和组合共用 |
| `factors/*.py` 插件接口 `compute(panel)` | 因子作者只写 Pandas 公式；Spark 只负责分发，不改契约 |
| Parquet 文件格式 | 列式、压缩、Hive/Spark/DuckDB 都能读 |
| 本地单机部署 | 课程演示环境，不引入云依赖 |

---

## 1. 分层与单一事实来源

### 1.1 分层职责

| 层 | 责任 | 只允许写入 | 只允许读取 |
| --- | --- | --- | --- |
| ODS | 原样落库，不做业务计算 | 接入作业 | DWD 作业 |
| DWD | 清洗、PIT 对齐、universe、标签、质检 | DWD 作业 | DWS 作业、回测 |
| DWS | 因子值、因子评估、权重、风格簇、频繁项集 | 因子/模型作业 | ADS 作业、回测、服务层 |
| ADS | 面向页面的小结果表 | 汇总作业 | DuckDB 服务层 |
| HBase | 单点查询快照 | 发布作业 | API 下钻 |

### 1.2 三条单一事实来源规则

1. 行情唯一来源是 `dwd.prices_clean`。ODS 只被 DWD 读，页面和回测不得直接读 ODS。
2. 因子值唯一来源是 `dws.factor_values`。策略、回测、模型、页面都读它，不各自重算。
3. 标签唯一来源是 `dwd.label_forward`。因子 IC、模型训练、回测信号使用同一份标签定义。

任何一条数据在页面上出现，都必须能追到 `run_id`、`data_snapshot` 和所在层。

---

## 2. 技术要求：集群与版本

### 2.1 版本基线

版本锁定在 `docker/versions.env`，所有容器和客户端读取同一份文件。

| 组件 | 版本 | JDK | 说明 |
| --- | --- | --- | --- |
| Ubuntu | 22.04 | — | 基础镜像 |
| Hadoop | 3.3.6 | 8 | HDFS + YARN + MapReduce；JDK 8 是 Hadoop 3.3 的稳定组合 |
| Hive | 3.1.3 | 8 | Metastore + HiveServer2；JDK 8 避免 Hive 3.1 在 JDK 11 上的兼容问题 |
| HBase | 2.2.7 | 8 | HMaster + RegionServer + 内置 ZooKeeper；与 Hive 3.1.3 的 HBase handler 匹配 |
| ZooKeeper | 3.5.7 | 8 | HBase 内置，不单独起服务 |
| Spark | 4.0.1 | 17 | 与仓库 `pyspark==4.0.1` 一致；作业提交到 YARN |
| PostgreSQL | 13 | — | Hive Metastore 元数据库；Hive 3.1.3 在 PG 15 上存在元数据查询兼容问题 |
| Python（执行器） | 3.12 | — | `hadoop-base`（NodeManager）和 `spark-client` 镜像内置，依赖版本与 `requirements.txt` 一致 |
| Python（宿主机） | 3.12 | — | 只跑 Web、DuckDB 和测试 |

版本组合在 Step 1 验证。任何版本变更必须同时更新 `docker/versions.env`、`README.md` 和课程材料中的版本页。

NodeManager 镜像同时安装 JDK 8（Hadoop/HBase）和 JDK 17（Spark executor）；Spark 配置显式指定 executor 的 `JAVA_HOME`，不能让它落到 JDK 8。

镜像统一基于 Ubuntu 22.04；JDK 8/17 用 Adoptium，Python 3.12 从 deadsnakes PPA 安装。

### 2.2 服务清单

| 服务 | 角色 | 端口 | 内存上限 |
| --- | --- | --- | --- |
| `nn-rm` | NameNode + ResourceManager | 9870、8088、9000 | 4g |
| `dn-nm-1` | DataNode + NodeManager | 9864、8042 | 4g |
| `dn-nm-2` | DataNode + NodeManager | 9864、8042 | 4g |
| `hive` | Metastore + HiveServer2 | 9083、10000 | 2g |
| `hbase` | HMaster + RegionServer | 16010、16030、16050 | 3g |
| `metastore-db` | PostgreSQL 13 | 5432（仅集群网） | 512m |
| `spark-client` | Spark driver + Python 3.12 | 无 | 2g |
| `spark-history` | Spark HistoryServer | 18080 | 1g |

`spark-client` 以 `sleep infinity` 常驻，作业按需 `docker compose exec` 进入。

网络：`quantlens-net`，bridge 模式。所有发布端口绑定 `127.0.0.1`，仅本机访问。
卷：

- `hdfs_namenode`、`hdfs_datanode_1`、`hdfs_datanode_2`
- `hive_metastore_pg`
- `spark_events`
- `hbase_data`（如 HBase 直接使用 HDFS，可不单独建卷）

绑定挂载：

- `./docker/conf` 只读挂载到各容器
- `./data/landing` 读写挂载到 `nn-rm` 和 `spark-client`，供 CSMAR 接入暂存
- `./data/ads` 读写挂载到 `nn-rm` 和 `spark-client`，供 ADS 回传
- `./:/workspace` 只读挂载到 `spark-client` 和两个 `dn-nm-*`，作业代码和因子插件直接从仓库加载
- 其他服务不挂载项目目录

资源下限：16GB 内存、8 vCPU、20GB 可用磁盘。低于 16GB 时停掉 `dn-nm-2`，单 DataNode 运行，`dfs.replication` 改 1，并下调 executor 内存。

### 2.3 关键配置

`docker/conf/hadoop/hdfs-site.xml`

- `dfs.blocksize=134217728`（128MB）
- `dfs.replication=2`（两个 DataNode；扩到三个再改 3）
- `dfs.namenode.name.dir=/hadoop/dfs/name`
- `dfs.datanode.data.dir=/hadoop/dfs/data`

`docker/conf/hadoop/yarn-site.xml`

- `yarn.nodemanager.resource.memory-mb=3072`
- `yarn.scheduler.maximum-allocation-mb=2048`
- `yarn.nodemanager.resource.cpu-vcores=2`
- `yarn.resourcemanager.hostname=nn-rm`

`docker/conf/hadoop/mapred-site.xml`

- `mapreduce.framework.name=yarn`
- `mapreduce.application.classpath` 指向 Hadoop 的 `mapreduce/*`、`yarn/*` 目录

`docker/conf/hive/hive-site.xml`

- `hive.metastore.uris=thrift://hive:9083`
- `javax.jdo.option.ConnectionURL=jdbc:postgresql://metastore-db:5432/metastore`
- `hive.exec.dynamic.partition=true`
- `hive.exec.dynamic.partition.mode=nonstrict`
- `hive.execution.engine=mr`（Hive 3.1.3 不官方支持 Spark 4；Hive 负责数仓与一条 MR 对照，批量计算由 Spark SQL 承担）
- Hive 容器设置 `HADOOP_HOME`、`HADOOP_MAPRED_HOME`，并挂载 `core-site.xml`、`hdfs-site.xml`、`yarn-site.xml`、`mapred-site.xml`，否则 MR 任务起不来

`docker/conf/hbase/hbase-site.xml`

- `hbase.rootdir=hdfs://nn-rm:9000/hbase`
- `hbase.cluster.distributed=true`
- `hbase.zookeeper.quorum=hbase`
- `hbase.zookeeper.property.dataDir=/hbase/zookeeper`
- `hbase.rest.port=16050`
- `hbase-env.sh` 设 `HBASE_MANAGES_ZK=true`；entrypoint 依次启动 HMaster、RegionServer、REST

`docker/conf/spark/spark-defaults.conf`

- `spark.master=yarn`
- `spark.submit.deployMode=client`
- `spark.executor.memory=1536m`
- `spark.executor.cores=2`
- `spark.executor.instances=2`
- `spark.sql.shuffle.partitions=16`
- `spark.sql.adaptive.enabled=true`
- `spark.pyspark.python=/usr/bin/python3`
- `spark.pyspark.driver.python=/usr/bin/python3`
- `spark.yarn.appMasterEnv.PYTHONPATH=/workspace`
- `spark.executorEnv.PYTHONPATH=/workspace`
- `spark.yarn.appMasterEnv.JAVA_HOME=/usr/lib/jvm/java-17`
- `spark.executorEnv.JAVA_HOME=/usr/lib/jvm/java-17`
- `spark.eventLog.enabled=true`
- `spark.eventLog.dir=hdfs:///spark-events`
- `spark.history.fs.logDirectory=hdfs:///spark-events`
- `spark.sql.catalogImplementation=hive`
- `spark.sql.parquet.compression.codec=snappy`
- `spark.sql.execution.arrow.pyspark.enabled=true`
- `spark.python.worker.memory=512m`
- `spark.sql.files.maxPartitionBytes=134217728`

### 2.4 客户端约定

集群作业不直接在宿主机跑，统一进 `spark-client` 容器，避免 Python 环境、Hadoop CLI 和 YARN 回连三类问题。

- `spark-client` 镜像内置 Spark 4.0.1、Python 3.12、pandas 3.0.3、numpy 2.4.6、pyarrow 24.0.0；不装 Hadoop CLI。
- NodeManager 镜像同样内置 Python 3.12 和同版本依赖；NodeManager 只读挂载 `/workspace`，executor 直接导入 `src/` 和 `factors/`，不需要 `--py-files` 或源码压缩包。
- 容器内环境变量：`HADOOP_CONF_DIR=/etc/hadoop/conf`、`SPARK_CONF_DIR=/etc/spark/conf`、`PYSPARK_PYTHON=/usr/bin/python3`、`PYTHONPATH=/workspace`；`/etc/spark/conf` 同时放 `spark-defaults.conf` 和 `hive-site.xml`。
- 提交命令：`docker compose exec -T spark-client spark-submit --master yarn --deploy-mode client /workspace/src/quantlens/<job>.py ...`。
- HDFS 操作统一走 `src/storage/hdfs.py`，内部执行 `docker compose exec -T nn-rm hdfs dfs ...`；`nn-rm` 是 JDK 8，CLI 版本与集群一致，避免 JDK 17 跑 Hadoop CLI 的兼容问题。
- staged 文件通过硬链接进入 `data/landing`（跨文件系统时复制），容器内路径为 `/data/landing`；上传用 `hdfs dfs -put`。
- ADS 回传用 `hdfs dfs -get /quantlens/ads/<table>/... /data/ads/<table>/`，`data/ads` 同时挂载到宿主机。
- HBase 访问统一走 HBase REST（`hbase rest start`，端口 16050），Python 用 `httpx`，不引入 Thrift 客户端。
- 宿主机只跑 Web 服务、DuckDB 查询和单元测试，使用 `~/.venv/bin/python`。
- 集群不可用时默认报错，不静默回退到本地；本地开发用 `QUANTLENS_COMPUTE_LOCAL=1` 显式开启。

---

## 3. 技术要求：存储分层

### 3.1 HDFS 目录

```
/quantlens/ods/<table>/year=YYYY/
/quantlens/dwd/<table>/year=YYYY/
/quantlens/dws/<table>/<partition...>
/quantlens/ads/<table>/as_of=YYYY-MM-DD/
/quantlens/meta/schema/<table>.json
/quantlens/meta/quality/<dt>/
/quantlens/archive/ads/            # 只读归档，不删除
```

### 3.2 分区与文件大小

| 表 | 分区键 | 目标文件大小 |
| --- | --- | --- |
| `ods.prices_daily` | `year` | 128–256MB |
| `ods.returns_daily` | `year` | 128–256MB |
| `ods.fs_*` | `year` | 32–128MB |
| `ods.analyst_*` | `year` | 64–128MB |
| `dwd.prices_clean` | `year` | 128–256MB |
| `dwd.fundamentals_pit` | `year` | 64–128MB |
| `dwd.analyst_pit` | `year` | 64–128MB |
| `dwd.label_forward` | `horizon`、`year` | 64–128MB |
| `dwd.universe` | `year` | 16–32MB |
| `dws.factor_values` | `factor`、`version`、`year` | 32–64MB |
| `dws.factor_eval_daily` | `factor`、`version`、`year` | 16–32MB |
| `dws.factor_weights` | `strategy_id`、`as_of` | 小文件 |
| `dws.style_cluster` | `year` | 16–32MB |
| `dws.factor_itemset` | `as_of` | 小文件 |
| `dws.factor_meta` | 无（小表） | 小文件 |
| `dws.factor_eval` | `as_of` | 小文件 |
| `dws.factor_corr` | `as_of` | 小文件 |
| `dws.pca_loadings` | `as_of` | 小文件 |
| `dws.pca_scores` | `year` | 64–128MB |
| `ads.*` | `as_of` | 小文件 |

`version` 必须进分区：因子值、因子评估都是版本化数据，新版本写入不能覆盖旧版本。

写入前按目标文件大小 `coalesce` 或 `repartition`，避免每分区大量小文件。

### 3.3 格式与命名

- 统一 Parquet + Snappy。
- 数据库：`ods`、`dwd`、`dws`、`ads`、`meta`。
- 表命名：`ods_<源表>`、`dwd_<域>`、`dws_<主题>`、`ads_<页面>`。
- 列名使用小写下划线；代码列 `string`，日期列 `date`，数值列 `double`，标志列 `boolean`。
- ODS 列名与 `data/staged/<table>` 的 Parquet schema 一致，不允许在 ODS 改名。

### 3.4 保留策略

- ODS、DWD、DWS 全部保留，属于研究证据。
- ADS 保留 3 年，更早分区移动到 `/quantlens/archive/ads/`，不删除。
- HBase 快照表 TTL 1095 天；latest 表不设 TTL。
- 不生成任何备份副本。

### 3.5 Schema 管理

- `src/quantlens/schema_export.py` 读取 staged Parquet schema，写出 `/quantlens/meta/schema/<table>.json`。
- DDL 由这些 JSON 生成，存放在 `docker/ddl/`。
- Schema 变更必须新建 DDL 版本并写迁移说明；禁止静默删列或改类型。

---

## 4. 技术要求：接入与 DWD

### 4.1 接入契约

- 输入：`data/staged/<table>/*.parquet`，由 `src/providers/` 现有同步流程产出。
- 上传前把文件硬链接到 `data/landing/<table>/`（跨文件系统时复制），容器内路径为 `/data/landing`。
- `src/quantlens/ingest_hdfs.py` 只上传发生变化的分区。
- 上传先写 `_tmp/<run_id>/`，完成后 `hdfs dfs -mv` 到正式路径；失败清理临时目录。
- 所有 HDFS 命令通过 `docker compose exec -T nn-rm hdfs dfs ...` 执行，宿主机不要求安装 Hadoop。
- 清单 `data/ingest_manifest.json` 记录 `run_id`、`table`、`partition`、`rows`、`sha256`、`completed_at`。
- 不上传任何凭据、配置、日志。
- 不在本地保留 staged 数据的副本。

### 4.2 幂等与原子性

- 每个 ODS 分区整体替换，不追加。
- 同一分区重跑必须产生相同行数和校验和。
- 下游只读取已发布分区；`_tmp` 和 `_blocked` 路径对下游不可见。
- 作业失败不改变已发布分区。

### 4.3 DWD 作业

| 作业 | 输出 | 关键规则 |
| --- | --- | --- |
| `spark_dwd_prices.py` | `dwd.prices_clean` | 按 `(code, date)` 去重，保留 `updated_at` 最新；校验 OHLC；统一复权收盘价 `adj_close`；保留 `total_mv`、`circ_mv`、`amount`、`turnover_rate`；补 `ret_wd`；标记涨跌停、停牌、退市；生成 `dwd.universe` |
| `spark_dwd_fundamentals.py` | `dwd.fundamentals_pit` | 长表 `code, ann_date, report_period, effective_date, statement, item, value`；`effective_date` 取实际可见日；禁止未来报告期 |
| `spark_dwd_analyst.py` | `dwd.analyst_pit` | 预期数据 PIT；`effective_date` 取发布日；按 `(code, date, item)` 去重；覆盖率写入质检 |
| `spark_dwd_industry.py` | `dwd.industry_pit` | 行业分类 PIT；缺失时向前沿用最后可见值；记录 `effective_date` |
| `spark_dwd_label.py` | `dwd.label_forward` | 字段 `code, date, horizon, ret, bench_ret, valid, valid_until`；`entry_offset=1`、`exit_offset=horizon+1`；`valid_until` 为退出日；无未来数据 |
| `spark_quality.py` | `dwd.quality_report` | 规则化质检，输出通过/失败、失败行数、样例 |

标签口径变更时新建 `label_version` 分区或新表，旧标签不覆盖；回测结果必须记录使用的是哪一版标签。`bench_ret` 固定用 `universe_equal_weight`；同簇基准在回测结果层单独计算，避免标签依赖 KMeans。

`dwd.universe` 规则：

- 上市满 120 个交易日。
- 非 ST、非退市整理。
- 当日有成交且成交额不低于 500 万元。
- 价格不低于 1 元。
- 记录 `in_universe` 与排除原因。

### 4.4 质量门禁

| 规则 | 阈值 | 处理 |
| --- | --- | --- |
| `prices` 重复 `(code,date)` | 0 | 阻断发布 |
| OHLC 非法比例 | < 0.1% | 阻断发布 |
| 标签覆盖率 | > 95% universe | 阻断发布 |
| 因子输入覆盖率 | > 90% | 告警并阻断该因子分区 |
| PIT 未来日期 | 0 | 阻断发布 |
| 每日 universe 数量 | 3000–6000 | 告警 |

### 4.5 失败处理

- 作业失败：不切换分区，下游继续读上一版完整分区。
- 质检失败：写报告，分区标记 `_blocked`，不发布。
- 重试安全：所有写入幂等。
- 错误结构：`{code, stage, partition, message, hint}`，写入 HDFS `/quantlens/meta/runs/<job>/<run_id>/error.json`。

---

## 5. 技术要求：Hive 数仓

### 5.1 Metastore 与数据库

- Metastore 使用 PostgreSQL 13，Hive 与 Spark 共享同一份 `hive-site.xml`。
- Spark 通过 `hive.metastore.uris` 连接同一 Metastore。Step 1 先验证 Spark 内置 Hive 客户端能否读写 Hive 3.1.3 的表和分区；不能则把 Hive 3.1.3 lib 打进 `spark-client` 和 NodeManager 镜像，用 `spark.sql.hive.metastore.jars=path` 指向 `/opt/hive/lib/*`。
- 数据库：`ods`、`dwd`、`dws`、`ads`、`meta`。
- 所有表为外部表，`LOCATION` 指向 HDFS；删除表不删除数据。

### 5.2 DDL 约定

- 分区列放在最后，类型统一。
- 表属性：`parquet.compression=snappy`。
- 分区写入后执行 `MSCK REPAIR TABLE`，或按清单 `ALTER TABLE ADD PARTITION`。
- 页面查询优先使用 `ads` 视图，不直接拼长 SQL。

示例：

```sql
CREATE EXTERNAL TABLE dws.factor_values (
  code string,
  date date,
  value double,
  std_value double,
  coverage double,
  run_id string
)
PARTITIONED BY (factor string, version string, year int)
STORED AS PARQUET
LOCATION '/quantlens/dws/factor_values';
```

### 5.3 引擎选择

- 批量 SQL 默认由 Spark SQL 执行，读取同一套 Metastore 表。
- HiveServer2 保留给课程 HQL 演示，`hive.execution.engine=mr`。Hive 3.1.3 不官方支持 Spark 4，强行切换会在编译执行计划时报类冲突。
- MR 只承担一条对照查询，用来做第 4 章的耗时对比；其余批量由 Spark SQL 执行。
- 在 PPT 中写明：Hive 负责元数据、表和 SQL 入口，Spark 负责批量计算，MR 只做对照。
- Hive 不用于页面即时查询。

### 5.4 Hive-HBase 集成

- `ods.profile_hbase` 映射 `ql:profile`。
- `dws.factor_snapshot_hbase` 映射 `ql:factor_snapshot`。
- Hive 容器需要 `hive-hbase-handler` 和与 HBase 服务端一致的 HBase client jars，通过 `HIVE_AUX_JARS_PATH` 加载；同时挂载同一份 `hbase-site.xml`，否则 handler 找不到 ZooKeeper 和根目录。
- 演示 HQL：

```sql
SELECT code, date, f['momentum_20']
FROM dws.factor_snapshot_hbase
WHERE code = '000001' AND date >= '2026-01-01';
```

- `ql:factor_snapshot` 的 rowkey 带盐，按 `code` 过滤的 Hive 查询会扫描全表；演示时限定日期范围，或额外建 `ql:factor_by_code`（rowkey `code|date`）供 Hive 查询。
- Hive-HBase handler 与 HBase 2.2.7 的兼容性在 Step 8 验证；不通过时优先调整 handler 依赖或降到 HBase 2.0.x，不跳过实验二。
- 实验二验收使用这两张表。

---

## 6. 技术要求：因子体系

### 6.1 插件契约不变

- 因子仍是 `factors/<name>.py` 中的 `compute(panel)`。
- `panel` 是 `dict[str, pandas.DataFrame]`，每个 DataFrame 是 `日期 × 证券`，索引为交易日，列为证券代码。
- 因子作者不需要知道 Spark；不得在因子内读写文件、访问网络或修改全局状态。
- `dependencies` 列出所需字段，Spark 作业只读取这些字段。

### 6.2 元数据扩展

在 `config/factors.yaml` 和 `FactorMetadata` 中增加：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `min_history` | 120 | 因子计算需要的历史交易日数，决定窗口重叠 |
| `window_days` | 60 | 每个 Spark 窗口包含的交易日数 |
| `scope` | `stock` | `stock` 或 `cross_section`，仅用于说明和校验 |
| `engine` | `pandas` | 预留；当前统一走 Pandas 窗口 |

`scope` 不改变执行方式：每个窗口都包含全市场证券，横截面因子同样可算。

### 6.3 Spark 执行模型

`src/quantlens/spark_factor_values.py`：

1. 从 `dwd.prices_clean` 读取交易日列表。
2. 按 `window_days` 切窗，每窗向前多取 `min_history` 天。
3. 读取因子 `dependencies` 对应字段，构造宽表 `(window_id, date, code, dep1..depN)`。
4. `groupBy('window_id').applyInPandas(run_window, schema)`。
5. `run_window` 内按字段 pivot 成 `日期 × 证券`，组装 `panel`，调用 `compute(panel)`。
6. 丢掉重叠的前 `min_history` 天，`melt` 成 `(code, date, value)`。
7. 写入 `dws.factor_values`，按 `factor`、`version`、`year` 动态分区覆盖；只覆盖当前版本分区，旧版本保留。

字段映射：

- `config/panel_fields.yaml` 把 panel 字段名映射到 DWD 表/列或 SQL 表达式，Spark 作业只按映射取数，不在因子代码里写 SQL。
- 首版映射：`adj_close_wd -> dwd.prices_clean.adj_close`；`net_profit_ttm_parent -> dwd.fundamentals_pit` 的 TTM 计算；`consensus_* -> dwd.analyst_pit` 的 PIT 值。
- 映射缺字段时作业失败，错误里带因子名和字段名，不做静默填 0。

内存保护：

- 估算 `window_dates × instruments × dependencies × 8 × 3`。
- 超过 executor 内存 50% 时自动减半 `window_days` 并告警。
- 单窗失败只影响该窗口，因子分区不发布。

确定性与一致性：

- 写入前按 `(factor, date, code)` 排序。
- 与现有 Pandas runner 做 200 只股票抽样对照：Pearson ≥ 0.999，最大绝对差 ≤ 1e-6。
- 因子值保留 6 位小数，避免浮点尾差导致重复运行不一致。

### 6.4 标准化与方向

- `std_value` 在 DWS 计算，规则来自因子元数据 `standardization`：
  - 横截面缩尾 1%/99%。
  - 行业中性：对行业哑变量回归取残差。
  - 市值中性：对对数市值回归取残差。
  - 横截面 zscore。
- `standardization` 为空时用默认口径：缩尾 1%/99% + 横截面 zscore；行业、市值中性必须显式声明。
- 缺失处理按 `missing_handling`：`drop` 不参与当日横截面；`fill` 用当日行业中位数；不得跨日填充。
- `value` 保留原值，供审计和重新标准化。
- `backtest/signal.py` 不再做标准化，只读 `std_value` 并应用 `direction`。
- 方向只允许 `positive` / `negative`，不得用样本内 IC 推断方向。

### 6.5 因子评估

`src/quantlens/spark_factor_eval.py`：

- 连接 `dws.factor_values`、`dwd.label_forward`、`dwd.universe`。
- 每日 rank IC：

```sql
WITH ranked AS (
  SELECT f.date, f.code,
         percent_rank() OVER (PARTITION BY f.date ORDER BY f.std_value) AS rf,
         percent_rank() OVER (PARTITION BY f.date ORDER BY l.ret) AS rl
  FROM dws.factor_values f
  JOIN dwd.label_forward l
    ON f.code = l.code AND f.date = l.date AND l.horizon = 5
  JOIN dwd.universe u
    ON f.code = u.code AND f.date = u.date AND u.in_universe
  WHERE f.factor = 'reversal_5' AND f.version = 'v1'
)
SELECT date, count(*) AS n, corr(rf, rl) AS rank_ic
FROM ranked
GROUP BY date;
```

指标：

- 平均 rank IC、IC 标准差、ICIR。
- IC 为正的日期占比。
- 季度同向比例（sign stability）。
- 覆盖率。
- 五分组平均收益、单调性。
- 换手：最高分位成员变化率。
- 衰减：horizon 1/5/10/20 的 IC。
- 因子相关性矩阵。

输出：

- `dws.factor_eval_daily`：逐日 IC。
- `dws.factor_eval`：汇总指标。
- `dws.factor_corr`：因子两两相关。
- `ads.factor_eval`：页面读取的小表。

评估窗口采用滚动：选因子用的区间与评估区间不重叠；锁定测试集只读一次，记录暴露。

### 6.6 因子库同步

- `src/quantlens/sync_factor_meta.py` 读取 `config/factors.yaml`，计算 `code_hash`，写入 `dws.factor_meta`。
- 注册表仍是唯一事实来源；Hive 表是只读镜像，供 SQL join。
- 因子版本变化新增一行，旧版本因子值仍可按 `version` 查询。
- 因子代码、参数变化必须升 `version`；同一版本只允许同一 `code_hash` 重跑，禁止用同版本覆盖不同实现。

---

## 7. 技术要求：模型与算法

### 7.1 模型身份规则

模型只能以两种身份存在：

1. 策略的 `weighting` 方法：`model_linear`、`model_logistic`。
2. 注册因子：`model_gbt`，有版本、`code_hash`、`run_id`。PCA 只做研究对照，不注册为生产因子。

不允许：

- 独立的“预测模型”页面。
- 独立的一套模型评估指标。
- 独立的每日模型跟踪调度。
- 模型自行定义特征集合、绕开因子库。

### 7.2 权重拟合

`src/quantlens/spark_weight_fit.py`：

- 输入：`dws.factor_values.std_value`（策略声明的因子）、`dwd.label_forward`、`dwd.universe`。
- 方法：

| 方法 | 计算 | 输出 |
| --- | --- | --- |
| `equal` | 1/n | 权重 |
| `ic` | 训练窗口内每日 rank IC 均值，按绝对值归一 | 权重 |
| `icir` | IC 均值 / IC 标准差，按绝对值归一 | 权重 |
| `model_linear` | 按日期去均值后 pooled OLS（吸收日期固定效应），系数归一 | 权重 |
| `model_logistic` | 标签为收益高于横截面中位数，逻辑回归，系数归一 | 权重 |

- 滚动训练：训练窗口 `train_years`，测试 1 年，步长 1 年；训练区间与测试区间不重叠。
- 权重和符号写入 `dws.factor_weights(strategy_id, as_of, factor, weight, method, train_start, train_end, run_id, metrics)`，按 `strategy_id`、`as_of` 分区覆盖：同一 `as_of` 重跑覆盖该分区，不同 `as_of` 保留历史。
- 对比结果写入 `ads.weighting_compare`：样本外 rank IC、最高分位收益、换手、最大回撤、目标函数值。
- 权重选择只看样本外结果，不看样本内 IC。

### 7.3 算法作业

| 算法 | 作业 | 输出 | 用途 |
| --- | --- | --- | --- |
| KMeans | `spark_kmeans.py` | `dws.style_cluster` | 风格分池、同簇基准、集中度约束 |
| FP-Growth | `spark_fpgrowth.py` | `dws.factor_itemset` | 频繁因子组合，供策略候选 |
| 线性回归 | `spark_weight_fit.py` | `dws.factor_weights` | 因子加权 |
| 逻辑回归 | `spark_weight_fit.py` | `dws.factor_weights` | 方向加权 |
| PCA | `spark_pca.py` | `dws.pca_loadings`、`dws.pca_scores` | 降维对照 |
| GBT | `spark_engine.py` | `model_gbt` 因子 | 非线性合成对照 |
| KNN | 不进入主线 | — | 仅可作为实验页，不占导航 |
| ALS | 不进入主线 | — | 仅可作为实验页，不占导航 |

KMeans：

- 输入：价值、成长、动量、质量、波动五类因子暴露，横截面标准化。
- 训练窗口拟合，下一年的日期分配簇；每年重拟合。
- `k` 取 5–8，用轮廓系数或 DB 指数选择，记录 `k` 和 inertia。
- 输出 `cluster_id`、距离、`run_id`。
- 用途：同簇等权基准、组合单簇上限 35%、可选按簇中性化。

FP-Growth：

- 事务：每月调仓日，每只股票当日的信号项集（因子处于最高分位则记为出现）。
- `minSupport=0.05`，`minConfidence=0.6`。
- 输出项集、支持度、置信度、提升度。
- 只作为候选生成；任何组合必须通过因子评估和回测才能进入策略。

PCA：

- 输入标准化因子，完整样本。
- 解释方差 ≥ 80% 的 `k`。
- 对比 PCA 因子与原始因子在加权和 GBT 上的样本外表现；保留更好的一组。
- 仅作为研究对照，不进入页面主流程。

GBT：

- 输入改为 `dws.factor_values`，不再使用 `spark_features.py` 的 30 个硬编码特征。
- 输出 `model_gbt` 因子，写回 `dws.factor_values`，有版本和 `run_id`。
- 由 `spark_factor_eval.py` 统一评估。
- 若样本外不如 `icir` 或 `model_linear`，标记 `status: retired`，保留负面结论。

### 7.4 下线项

| 现有 | 处理 |
| --- | --- |
| 前端“预测模型”页 | 删除导航和路由；不保留入口 |
| `src/quantlens/tracking.py` | 下线每日模型跟踪；由 `spark_daily.py` 在数据同步后计算因子值和策略信号 |
| `src/quantlens/model_ops.py` | 拆分：预测写因子值或权重；`allocate` 移到 `portfolio/construct.py` |
| `src/quantlens/spark_features.py` | 保留为 `model_gbt` 的特征库，不作为主因子来源 |
| `/api/research`、`/api/rolling`、`/api/forecasts`、`/api/tracking`、`/api/allocation` | 先下线路由，后端保留到前端切换完成并验证后删除 |

---

## 8. 技术要求：策略、回测、组合

### 8.1 策略配置

`FactorSpec.weighting` 允许值：

- `equal`
- `ic`
- `icir`
- `model_linear`
- `model_logistic`

`FactorRef` 保留 `name`、`weight_override`、`direction_override`。

策略 YAML 新增：

```yaml
constraints:
  max_weight: 0.10
  max_cluster_weight: 0.35
  max_industry_weight: 0.30
  cash_reserve: 0.10
  max_positions: 30
risk:
  regime_filter:
    enabled: true
    breadth_lookback: 60
    breadth_low: 0.40
    breadth_crisis: 0.30
    exposure_low: 0.50
    exposure_crisis: 0.25
benchmark: same_cluster_equal_weight
```

### 8.2 目标函数与协议

`config/backtest_protocol.yaml` 升级为 v2，v1 保持锁定可复现。

```yaml
schema_version: 2
objective:
  primary: calmar
  constraints:
    max_drawdown: 0.20
    max_turnover: 6.0
benchmark: same_cluster_equal_weight
benchmark_fallback: universe_equal_weight
execution_delay_days: 1
price_anchor: next_close
fill_assumption: all_or_nothing
slippage_bps: 10
commission_override_rate: 0.00008
```

规则：

- `primary` 只允许一个：`calmar`、`sharpe`、`total_return`；年化按 244 个交易日。
- 最大回撤是约束，不是目标；`max_turnover` 指年化单边换手率。
- 基准由协议锁定，策略 YAML 的同名字段只做展示，不覆盖协议；要换基准必须新建协议版本。
- 协议变更必须新建版本并重新锁定，旧运行保持原 `protocol_hash`。
- 冠军策略按主目标排序，满足全部约束；同分先比最大回撤，再比换手。
- `same_cluster_equal_weight` 依赖 `dws.style_cluster` 覆盖回测区间；缺失日期退回 `universe_equal_weight`，结果里标记 `benchmark_fallback_used`。

### 8.3 回测输入

- 信号来自 `ads.strategy_signal` 或 `dws.factor_values` + `dws.factor_weights`。
- 逐日撮合仍在 Python；Spark 只负责生成信号和权重。
- 执行价 `next_close`，T+1，涨跌停/停牌/退市规则沿用 `src/market_rules/`。
- 费用：佣金 0.8bp、最低佣金、印花税、滑点 10bp。
- 输出：
  - `ads.backtest_summary`
  - `ads.backtest_nav`
  - `ads.attribution`
- 每次运行记录：`data_snapshot`、因子版本、权重 `run_id`、`protocol_hash`、代码哈希、随机种子。

### 8.4 归因

- 因子贡献 = 权重 × 因子在持有期的收益。
- 残差 = 选股/择时未解释部分。
- 每期和累计都写 `ads.attribution`。
- 贡献之和 + 残差 = 组合收益，误差 ≤ 1e-6。

### 8.5 组合构建

`src/portfolio/construct.py`：

1. 从策略信号取候选，按分数排序，取 `top_n`。
2. 过滤不可交易、停牌、涨跌停无法买入的标的。
3. 权重按分数比例分配。
4. 应用单票上限、单簇上限、行业上限；超限部分按分数再分配，迭代到收敛或达到 20 次；最后扣现金保留。
5. 应用市场广度风控：宽度低于阈值时按比例降低总仓位。
6. 生成目标持仓，写 `ads.portfolio_target`。
7. 发布到 HBase：`ql:signal_latest`、`ql:holding_snapshot`。

风控口径：

- 市场广度 = universe 中收盘价高于 60 日均线的比例。
- 广度 < 0.40：总仓位 × 0.50。
- 广度 < 0.30：总仓位 × 0.25。
- 阈值写在策略 YAML，不硬编码。

---

## 9. 技术要求：服务层与 API

### 9.1 ADS 同步

- `src/quantlens/sync_ads.py` 在每个批次结束后执行。
- 用 `hdfs dfs -get` 把 `ads/<table>/as_of=...` 拉到 `data/ads/<table>/`。
- DuckDB 在 `data/ads/*.parquet` 上建视图。
- Web 进程不连接 HDFS；HDFS 故障不影响页面读上一版 ADS。
- 同步完成写 `data/ads_manifest.json`，页面据此显示 `data_as_of`。
- 同步的 ADS 表：`ads.factor_meta`、`ads.factor_eval`、`ads.factor_values`、`ads.factor_weights`、`ads.strategy_signal`、`ads.backtest_summary`、`ads.backtest_nav`、`ads.attribution`、`ads.portfolio_target`、`ads.weighting_compare`、`ads.audit_log`。Web 只读 `data/ads/`，`dws.*` 的“提取”也先落到这里。

### 9.2 DuckDB 定位

- 只读 ADS 小表和本地 `data/staged` 遗留表。
- 负责页面即时查询、分页、排序。
- 不承担全市场批量计算。
- 不直接读 HDFS。

### 9.3 API 清单

| 接口 | 数据源 | 说明 |
| --- | --- | --- |
| `GET /api/overview` | ADS | 数据覆盖、市场广度、任务状态 |
| `GET /api/factors` | `ads.factor_meta` | 因子列表 |
| `GET /api/factors/{name}` | `ads.factor_meta` + `ads.factor_eval` | 因子详情 |
| `GET /api/factors/{name}/values` | `ads.factor_values` | 指定日期和证券 |
| `GET /api/factors/{name}/evaluation` | `ads.factor_eval` | 评估指标 |
| `POST /api/screen` | ADS | 条件选股，保留 |
| `GET /api/strategies` | 配置 + ADS | 策略列表 |
| `GET /api/strategies/{id}/weights` | `ads.factor_weights` | 当前权重 |
| `POST /api/backtests` | Python 引擎 | 运行回测 |
| `GET /api/backtests/{run}` | ADS | 汇总与净值 |
| `GET /api/portfolio/{id}/candidates` | ADS + 候选池 | 候选 |
| `GET /api/portfolio/{id}/holdings` | HBase + ADS | 持仓 |
| `GET /api/portfolio/{id}/attribution` | ADS | 归因 |
| `GET /api/settings/data` | `ads_manifest` | 数据状态 |
| `GET /api/settings/cluster` | HDFS/YARN 健康 | 集群状态 |
| `GET /api/settings/audit` | SQLite 镜像 | 审计 |

下线接口：`/api/research`、`/api/rolling`、`/api/forecasts`、`/api/tracking`、`/api/allocation`。

### 9.4 响应契约

- 所有响应带 `data_as_of`、`layer`、`run_id`、`source_uri`（适用时）。
- 错误沿用 `ContractViolation` 结构：`code`、`message`、`suggested_action`。
- 分页参数统一 `limit`、`offset`，默认 `limit=100`。
- 金额、比例、日期格式与现有 `fmt.js` 一致。

### 9.5 服务指标

- 条件选股 p95 < 1s。
- 个股下钻 p95 < 500ms。
- 因子详情 p95 < 800ms。
- 工作台 p95 < 500ms。
- 批量作业不承诺墙钟时间，只承诺幂等和完整性。

---

## 10. 调整要求：前端

### 10.1 最终导航

```
研究主线
  工作台        dashboard
  条件选股      screen
  因子库        factors       子标签：因子列表 / 迭代实验
  策略回测      backtest      子标签：策略配置 / 回测结果
  组合跟踪      portfolio     子标签：候选池 / 持仓 / 归因
设置
  settings                    子标签：数据同步 / 接口 / 任务与集群 / 记录
```

可见入口 6 个。

### 10.2 路由表

| 路由 | 可见 | 处理 |
| --- | --- | --- |
| `dashboard` | 是 | 保留 |
| `screen` | 是 | 保留 |
| `factors` | 是 | 保留，加子标签 |
| `backtest` | 是 | 保留，加子标签 |
| `portfolio` | 是 | 保留，加子标签 |
| `settings` | 是 | 新建壳 |
| `stocks` | 否 | 保留路由，作为下钻页 |
| `assistant` | 否 | 改为顶栏按钮 + 右侧面板 |
| `models` | 否 | 下线 |
| `market` | 否 | 并入工作台 |
| `watch` | 否 | 并入组合跟踪候选池 |
| `experiments` | 否 | 并入因子库子标签 |
| `strategy` | 否 | 并入策略回测子标签 |
| `data` | 否 | 并入设置 |
| `ai-config` | 否 | 并入设置 |
| `system` | 否 | 并入设置 |
| `review` | 否 | 并入设置“记录” |
| `journal` | 否 | 并入个股/因子详情的“研究记录” |
| `overview` | 否 | 遗留页，确认无引用后删除 |

### 10.3 工作台改动

- 标题改为“市场与研究概览”。
- 统计项：最新交易日、市场平均涨跌、覆盖证券、因子评估数、待处理任务、数据覆盖天数。
- 按钮：“更新数据”指向 `settings` 数据页；“条件选股”指向 `screen`；“研究因子”指向 `factors`。
- 卡片：市场广度（保留）、数据状态（新增）、最近研究（改为因子评估记录）、需要处理。
- 删除所有指向 `models` 的按钮、文案和统计。

### 10.4 子标签

- `factors`：第一页复用现有因子列表和详情；第二页复用 `experiments` 的列表和运行入口。
- `backtest`：第一页复用 `strategy` 的配置表单；第二页复用 `backtest` 的结果面板。
- `portfolio`：候选池复用 `/watch`；持仓复用现有面板；归因新增表格和净值拆解。
- `settings`：新建 `settings` 壳组件，四个子页懒加载 `data`、`ai-config`、`system`、`review` 的现有 `mount`；“记录”页只放审计和评审，研究记录随个股/因子详情走。
- 子标签使用 `role="tablist"`、`aria-selected`，支持左右方向键。

### 10.5 个股下钻

- 选股结果、因子详情、持仓列表中的证券代码都可点击。
- 点击设置 `shared.stock`，跳转 `stocks`。
- `stocks` 页保留，不在导航显示。
- 页面顶部提供“返回上一页”，使用 `history.back()`。
- 下钻页展示：价格与成交、财务与经营、预期与机构、因子值、最新信号、研究记录。

### 10.6 研究助手

- 顶栏增加“研究助手”按钮。
- 点击打开右侧面板，复用 `chat-stream` 和现有助手表单。
- 路由变化时关闭面板。
- 不再占用独立路由。

### 10.7 死链清理

搜索并清理：

```
#models  #market  #watch  #experiments  #strategy
#data  #review  #ai-config  #system  #journal
data-go="models"
```

同时更新：

- `app.js` 的 `LENS`、`PAGES`、`READ_AHEAD`。
- `lens.js` 的 `dashboard`、`models`、`market`、`watch` 引用。
- 所有 `ctx.bus.emit('route', ...)` 目标。

### 10.8 响应式与主题

- 400px 宽度下，子标签横向滚动，表格放在独立滚动容器，页面主体不横向滚动。
- 主题令牌沿用 `theme.css`，不新增颜色。
- 滚动条继续使用淡色中性滑块。
- 深色/浅色均检查对比度。

---

## 11. 调整要求：代码与配置改动清单

| 文件/目录 | 改动 |
| --- | --- |
| `docker/` | 新增 `compose.yaml`、`versions.env`、`conf/`、`init/`、`ddl/`、`Dockerfile*` |
| `src/storage/hdfs.py` | 新增 HDFS CLI 封装：`put/get/ls/exists/mv/rm` |
| `src/storage/hbase.py` | 新增 HBase REST 封装：`get/put/scan/delete` |
| `src/quantlens/ingest_hdfs.py` | 新增 staged → ODS 上传、清单、幂等 |
| `src/quantlens/schema_export.py` | 新增 Parquet schema 导出 |
| `src/quantlens/spark_dwd_prices.py` | 新增 DWD 行情清洗 |
| `src/quantlens/spark_dwd_fundamentals.py` | 新增 PIT 财务长表 |
| `src/quantlens/spark_dwd_analyst.py` | 新增 PIT 预期数据 |
| `src/quantlens/spark_dwd_industry.py` | 新增 PIT 行业 |
| `src/quantlens/spark_dwd_label.py` | 新增标签表 |
| `src/quantlens/spark_quality.py` | 新增质检规则 |
| `src/quantlens/spark_factor_values.py` | 新增因子窗口计算 |
| `src/quantlens/spark_factor_eval.py` | 新增因子评估 |
| `src/quantlens/spark_weight_fit.py` | 新增权重拟合 |
| `src/quantlens/spark_kmeans.py` | 新增风格聚类 |
| `src/quantlens/spark_fpgrowth.py` | 新增频繁项集 |
| `src/quantlens/spark_pca.py` | 新增 PCA 对照 |
| `src/quantlens/sync_ads.py` | 新增 ADS 同步 |
| `src/quantlens/sync_factor_meta.py` | 新增注册表同步 |
| `src/quantlens/spark_daily.py` | 新增每日因子与信号任务 |
| `src/quantlens/spark_features.py` | 保留为 `model_gbt` 特征库 |
| `src/quantlens/spark_engine.py` | 改为读 `dws.factor_values`，产出 `model_gbt` |
| `src/quantlens/model_ops.py` | 拆分，移除独立产品逻辑 |
| `src/quantlens/tracking.py` | 下线模型跟踪 |
| `src/quantlens/data.py` | 改读 ADS 提取 |
| `src/quantlens/screening.py` | 改读 ADS 提取 |
| `src/panel/aggregates.py` | 迁移到 Spark SQL；Pandas 保留对照 |
| `src/panel/builder.py` | 保留 Pandas 对照；生产路径改 Spark |
| `src/factors/registry.py` | 增加 `min_history`、`window_days`、`scope`、`engine` |
| `src/factors/runner.py` | 保持 Pandas 路径，供对照和本地开发 |
| `config/factors.yaml` | 补齐新元数据 |
| `config/labels.yaml` | 新增标签定义 |
| `config/panel_fields.yaml` | 新增 panel 字段到 DWD 的映射 |
| `src/strategy/models.py` | 扩展 `weighting` 枚举和约束结构 |
| `config/strategies/*.yaml` | 补 `constraints`、`risk`、`benchmark` |
| `config/backtest_protocol.yaml` | 新增 v2，v1 保持锁定 |
| `config/champion_policy.yaml` | 改为按主目标 + 约束排序 |
| `src/backtest/signal.py` | 读 `std_value`，移除标准化 |
| `src/backtest/engine.py` | 读 DWS/ADS 信号和权重 |
| `src/backtest/metrics.py` | 增加目标函数与约束计算 |
| `src/portfolio/construct.py` | 新增组合构建与风控 |
| `src/portfolio/recommend.py` | 改为调用 `construct` |
| `src/lifecycle/models.py` | `DATA_SNAPSHOT` 增加 HDFS URI、分区、校验和 |
| `src/lifecycle/champion.py` | 按新目标排序 |
| `src/webui/frontend/components/rail/rail.html` | 改为 6 项 |
| `src/webui/frontend/app.js` | 更新 `LENS`、`PAGES`、`READ_AHEAD` |
| `src/webui/backend/routes/` | 新增因子评估、权重、组合、归因路由；下线 research/rolling/forecast/tracking/allocation |
| `src/webui/backend/routes/__init__.py` | 注册新路由，移除下线路由 |
| `src/webui/frontend/components/lens/lens.js` | 下线 `models`/`market`/`watch`；改工作台 |
| `src/webui/frontend/components/factors/` | 增加子标签 |
| `src/webui/frontend/components/backtest/` | 增加子标签 |
| `src/webui/frontend/components/portfolio/` | 增加子标签 |
| `src/webui/frontend/components/settings/` | 新增壳 |
| `src/webui/frontend/components/topbar/` | 增加助手按钮 |
| `README.md` | 更新架构、启动、目录说明 |
| `tests/` | 新增数据、因子、回测、前端导航契约测试 |

---

## 12. 治理与非功能要求

### 12.1 血缘与快照

- 每条 DWS/ADS 记录带 `run_id`、`version`、`data_snapshot`。
- `DATA_SNAPSHOT` 记录 HDFS URI、分区集合、行数、校验和。
- 页面展示的每个数字都能追到快照和作业。

### 12.2 审计

- SQLite 保留为事务审计库，保证账户和操作记录一致性。
- 每日导出到 `ads.audit_log`，供分析查询。
- 不新增“模型”对象类型；模型运行记为 `EVALUATION_RUN` 或 `FACTOR_VERSION`。

### 12.3 可复现

- 同一数据快照 + 配置 + 代码哈希 + 随机种子 → 相同指标。
- 记录 `protocol_hash`、因子 `code_hash`、Spark 版本、HDFS 快照。
- 锁定测试集只读一次，记录暴露。

### 12.4 资源

- Docker 至少 16GB 内存、8 vCPU、20GB 磁盘。
- YARN 单队列 `default`，不启用抢占；`spark-client` 常驻占 2GB。
- Spark executor 1.5GB、2 核、2 个实例。
- HBase 堆 1GB，Hive 堆 1GB。
- Python worker 512MB，开启 Arrow。

### 12.5 安全

- 所有 UI 端口绑定 `127.0.0.1`。
- 集群网络仅容器内互通。
- 凭据保留在本地配置或密钥存储，不上传 HDFS，不写入日志。
- 不开放公网端口。

### 12.6 日志与错误

- 作业日志写 HDFS `/quantlens/meta/runs/<job>/<run_id>/`，同时进 Spark History。
- 失败作业写结构化错误：`code`、`stage`、`partition`、`message`、`hint`。
- 集群不可用时明确报错并给修复提示，不静默回退。
- 本地开发用 `QUANTLENS_COMPUTE_LOCAL=1` 显式切换。

---

## 13. 测试与验收

### 13.1 测试矩阵

| 测试 | 范围 | 通过标准 |
| --- | --- | --- |
| Schema 契约 | ODS 列与 staged 对比 | 完全一致 |
| 幂等 | 同一分区重跑 | 行数、校验和一致 |
| DWD 去重 | `prices` | 重复 0 |
| PIT | 财务、行业、标签 | 未来日期 0 |
| 因子一致性 | 200 只股票，Pandas vs Spark | Pearson ≥ 0.999，最大差 ≤ 1e-6 |
| IC 一致性 | `reversal_5` 与现有评估 | 差异 ≤ 0.002 |
| 权重无前视 | 训练/测试区间 | 不重叠 |
| 回测可复现 | 同输入重跑 | 指标差 ≤ 1e-6 |
| 集群冒烟 | HDFS/Hive/HBase/Spark | 全部通过 |
| Hive-HBase | HQL 查询 | 返回行 |
| API 契约 | 新增接口 | 状态码、字段齐全 |
| 前端导航 | 可见路由 | 正好 6 个 |
| 死链 | grep + 点击 | 0 |
| 响应式 | 400px | 页面主体不横向滚动 |

### 13.2 课程对齐检查

| 课程内容 | 项目落点 |
| --- | --- |
| 第 2 章 数据预处理 | DWD 清洗、质检、PCA 对照 |
| 第 3 章 Linux/Docker | Docker Compose 集群、Ubuntu 镜像 |
| 第 4 章 Hadoop | HDFS 存储、YARN 调度、MR/Spark 同任务对照 |
| 第 5 章 HBase | 点查表、rowkey 设计、Hive-HBase 集成 |
| 第 6 章 Hive | ODS/DWD/DWS/ADS 四层外部表 |
| 第 7 章 Spark | Spark SQL、Spark on YARN、MLlib |
| 第 8–12 章 数据挖掘 | 线性/逻辑回归、KMeans、FP-Growth、PCA |
| 第 11 章 KNN/CF | 可选实验：KNN 找相似股票、ALS 做因子推荐；不进入主线，不占导航 |

课程材料必须包含：

- 一张主线架构图。
- 一张替换前后对照表。
- 一张 MR/Spark 同任务耗时表。
- 一张加权方法样本外对比表。
- 集群、Hive、HBase、Spark 作业的截图或录屏。

---

## 14. 实施步骤

步骤只给顺序和依赖，不给时间。

### Step 1 版本与连通性验证

- 目标：锁定版本组合，证明 HDFS、YARN、Hive、HBase、Spark 能互通。
- 改动：`docker/versions.env`、最小 `compose.yaml`、各服务基础镜像。
- 交付：可启动的最小集群。
- 验收：`spark-client` 内 HDFS put/get；Hive 建库建表；HBase create/put/get/scan；`docker compose exec -T spark-client spark-submit --master yarn` 跑通 wordcount；Spark SQL 读到 Hive 表；版本记录进 `README.md`。

### Step 2 集群编排与配置固化

- 目标：一条命令起集群，配置可复现。
- 改动：完整 `compose.yaml`、`conf/`、`init/`、健康检查、卷、网络、`ddl/` 目录。
- 交付：集群启动脚本和配置。
- 验收：`docker compose up -d` 全部 healthy；`spark-client` 能看到 `/workspace` 和 `/data`；重启保留数据；`down` 不带 `-v` 保留卷；`/quantlens` 目录自动创建。

### Step 3 HDFS 与 ODS 落库

- 目标：把 staged Parquet 原样搬进 HDFS，幂等可重跑。
- 改动：`src/storage/hdfs.py`、`ingest_hdfs.py`、`schema_export.py`、清单文件。
- 交付：ODS 表和接入清单。
- 验收：ODS 行数与 staged 一致；分区裁剪生效；重跑校验和一致；临时目录清理。

### Step 4 DWD 清洗、PIT、标签、质检

- 目标：DWD 干净、PIT 正确、标签唯一。
- 改动：六个 DWD 作业、`config/labels.yaml`。
- 交付：`dwd.prices_clean`、`dwd.fundamentals_pit`、`dwd.analyst_pit`、`dwd.industry_pit`、`dwd.label_forward`、`dwd.quality_report`、`dwd.universe`。
- 验收：重复 0；OHLC 非法 < 0.1%；PIT 抽样核对；标签无前视；质检报告生成；universe 数量在区间内。

### Step 5 Hive 数仓与表目录

- 目标：四层外部表可用，Spark 与 Hive 看到同一套表。
- 改动：DDL、`hive-site.xml`、`spark-defaults.conf`、分区修复、ADS 视图。
- 交付：`ods`、`dwd`、`dws`、`ads`、`meta` 五个库。
- 验收：`show databases`；`show partitions` 正确；计数与 HDFS 一致；Spark SQL 与 HiveQL 查询结果一致。

### Step 6 Spark 因子计算

- 目标：因子值在 Spark 上算，插件契约不变。
- 改动：`spark_factor_values.py`、因子元数据、内存保护。
- 交付：`dws.factor_values`，覆盖现有 10 个因子。
- 验收：200 只股票一致；10 个因子产出；重跑幂等；单窗失败隔离；执行器 Python 版本与 `requirements.txt` 一致。

### Step 7 因子评估与因子库同步

- 目标：评估口径统一，因子库可查。
- 改动：`spark_factor_eval.py`、`sync_factor_meta.py`、评估表。
- 交付：`dws.factor_eval_daily`、`dws.factor_eval`、`dws.factor_corr`、`ads.factor_eval`。
- 验收：`reversal_5` IC 与现有评估一致；评估表有数据；因子列表接口返回指标。

### Step 8 HBase 点查与 Hive-HBase

- 目标：单点查询可用，实验二集成完成。
- 改动：HBase 表、`src/storage/hbase.py`、发布作业、Hive 外部表。
- 交付：`ql:factor_snapshot`、`ql:signal_latest`、`ql:holding_snapshot`、`ql:profile`。
- 验收：单行 get/scan < 50ms；HQL 查询返回行；持仓和信号可查。
- 兼容性：Hive-HBase handler 与 HBase 2.2.7 不通过时，优先调整 handler 依赖或降到 HBase 2.0.x，不跳过实验二。

### Step 9 权重拟合与算法作业

- 目标：权重和算法产出可比较、可复现。
- 改动：`spark_weight_fit.py`、`spark_kmeans.py`、`spark_fpgrowth.py`、`spark_pca.py`、GBT 改造。
- 交付：`dws.factor_weights`、`dws.style_cluster`、`dws.factor_itemset`、`dws.pca_*`、`model_gbt`。
- 验收：权重归一；训练/测试不重叠；样本外对比表生成；簇稳定；项集有业务解释；GBT 作为因子被统一评估。

### Step 10 策略配置与回测统一

- 目标：从策略配置到回测一条链。
- 改动：`FactorSpec` 枚举、策略 YAML、协议 v2、同簇基准、归因。
- 交付：可运行的策略和回测结果。
- 验收：同一信号、同一协议下，Spark 信号 + Python 回测的结果与现有 Pandas 路径一致（费用和成交规则相同）；目标函数和约束记录；归因加总一致。

### Step 11 组合构建与持仓发布

- 目标：持仓建议可生成、可追溯。
- 改动：`portfolio/construct.py`、约束、风控、HBase 发布。
- 交付：`ads.portfolio_target` 和 HBase 持仓。
- 验收：权重和 ≤ 1 − 现金；单票/单簇/行业上限生效；风控按广度降仓；持仓查询返回最新。

### Step 12 ADS 同步与服务层 API

- 目标：页面数据来自 ADS/HBase，接口带溯源。
- 改动：`sync_ads.py`、DuckDB 视图、新增接口、下线模型接口。
- 交付：服务层 API。
- 验收：选股 p95 < 1s；个股下钻 p95 < 500ms；响应带 `data_as_of` 和 `run_id`。

### Step 13 前端导航与页面重构

- 目标：6 个入口，页面与主线一致。
- 改动：`rail.html`、`app.js`、`lens.js`、子标签、设置壳、顶栏助手、死链清理。
- 交付：新导航和页面。
- 验收：可见路由正好 6 个；隐藏路由可用；无死链；前端契约测试通过；400px 无横向滚动。

### Step 14 测试、验收与课程材料

- 目标：端到端验收，材料与实测一致。
- 改动：测试用例、演示脚本、架构图、对比表。
- 交付：测试报告和 PPT 素材。
- 验收：完整演示跑通；每个数字有来源；课程对齐检查表全部勾选。

### Step 15 清理与收口

- 目标：删除废弃入口和死代码，文档与实现一致。
- 改动：移除模型页、旧路由、旧本地集群演示路径；更新 README；确认没有备份文件。
- 交付：干净的仓库。
- 验收：`grep` 无死路由；测试通过；README 准确；`git status` 无临时产物；不生成 `.bak`。

### 14.1 依赖关系

- Step 1 → 2 → 3 → 4 → 5 → 6 → 7 顺序执行。
- Step 8 依赖 Step 5，可与 Step 6–7 并行。
- Step 9 依赖 Step 7。
- Step 10 依赖 Step 9 和 Step 4。
- Step 11 依赖 Step 10。
- Step 12 依赖 Step 11。
- Step 13 的“只删入口”部分可以提前做；“子标签和设置壳”依赖 Step 12。
- Step 14 依赖 Step 13。
- Step 15 最后执行。

---

## 15. 风险与回退

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 版本不兼容（Hive/Spark/HBase/JDK） | 集群起不来 | Step 1 先做兼容性验证；每个服务独立 JDK；版本锁在 `versions.env` |
| Spark 4.0.1 与 pandas 3.0.3 的 Arrow UDF 组合 | 因子计算出错或结果偏差 | Step 6 先跑 200 只股票对照；不兼容时 executor 固定 pandas 2.2.x，并保持纯 Pandas 语义 |
| 内存不足 | 作业 OOM、集群不稳 | 16GB 下限；executor 1.5GB；窗口 60 天；冒烟先限 500–1000 只股票 |
| 小文件过多 | NameNode 压力、查询慢 | 按年分区；写入前 `coalesce`；控制 `maxPartitionBytes` |
| Pandas UDF 内存 | 窗口任务失败 | 内存估算保护；自动缩小窗口；Arrow 开启 |
| shuffle 倾斜 | 作业变慢 | 维度表 broadcast；时间窗分区；避免按证券重分区 |
| Hive 延迟 | 演示卡顿 | 页面只读 ADS；Hive 只做批量和一条 HQL 演示；提前预热 |
| 前视偏差 | 结果虚高 | PIT 测试；`valid_until`；滚动训练；锁定测试集 |
| 过拟合 | 样本外失效 | 只看样本外；算法只做因子工具；保留负面结论 |
| 口径漂移 | 同一因子多个值 | DWD 单一事实来源；`std_value` 只在 DWS 算；旧路径只读 |
| UI 回归 | 页面打不开、死链 | 前端契约测试；路由扫描；先隐藏入口，再切页面 |
| 范围膨胀 | 主线失焦 | 算法只以权重或因子身份存在；不新增独立模块 |
| 误删业务数据 | 研究证据丢失 | 只增不删；ADS 归档不清理；不创建备份副本 |

回退顺序：

1. 服务层回退：`data/ads` 读上一版，页面不受影响。
2. 计算层回退：`config/compute.json` 切到 `local`，用现有 Pandas 路径重算。
3. 存储层回退：旧 `data/staged`、`data/matrix_cache` 保留到对齐完成，只读使用。
4. UI 回退：还原 `rail.html` 和 `app.js` 的导航，隐藏新入口。
5. 数据不删除，HDFS 写入为增量或分区覆盖，回退不依赖备份。

---

## 16. 交付物

### 16.1 代码与配置

- `docker/`：`compose.yaml`、`versions.env`、`conf/`、`init/`、`ddl/`、`Dockerfile*`。
- `src/storage/hdfs.py`、`src/storage/hbase.py`。
- `src/quantlens/` 下全部新作业。
- `config/labels.yaml`、`config/factors.yaml` 更新、`config/strategies/*.yaml` 更新、`config/backtest_protocol.yaml` v2。
- `src/portfolio/construct.py`。
- 前端 `rail.html`、`app.js`、`settings` 壳、子标签。
- `tests/` 新增测试。

### 16.2 文档

- `PLAN.md`（本文件）。
- `README.md` 更新：架构图、启动方式、目录说明、集群依赖。
- `docker/README.md`：集群启停、端口、常见故障。
- `docker/ddl/README.md`：表清单、分区、字段口径。

### 16.3 课程材料

- 主线架构图。
- 替换前后对照表。
- MR/Spark 同任务耗时表。
- 加权方法样本外对比表。
- 集群、Hive、HBase、Spark 作业截图或录屏。
- 演示脚本：从数据更新到持仓建议，按主线顺序走一遍。

---

## 17. 验收总则

1. 页面上每个数字都能追到 ODS → DWD → DWS → ADS 或 HBase，并带 `run_id`。
2. 不存在独立的“预测模型”页面；模型只以权重方法或注册因子存在。
3. 可见导航正好 6 个；隐藏路由只能通过下钻或面板进入。
4. 课程第 2–10、12 章的核心主题都有真实落点；第 11 章的 KNN/CF 以可选实验保留，不进入主线。
5. 同一数据快照 + 配置 + 代码哈希重跑，指标一致。
6. 全程不生成 `.bak`、备份压缩包或恢复副本；业务数据只增不删。
