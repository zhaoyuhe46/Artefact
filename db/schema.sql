-- 序隅 XUYU：一个 Postgres 库，两个 schema。
--   brand：店铺 / 品牌知识（结构化事实 + 向量化的品牌文本块）
--   crm  ：客户画像、购买记录、关键日期、敏感事项、对话历史
CREATE EXTENSION IF NOT EXISTS vector;

DROP SCHEMA IF EXISTS brand CASCADE;
DROP SCHEMA IF EXISTS crm CASCADE;
CREATE SCHEMA brand;
CREATE SCHEMA crm;

-- ---------- brand ----------
CREATE TABLE brand.products (
  sku       text PRIMARY KEY,
  name      text NOT NULL,
  category  text NOT NULL,
  price     integer NOT NULL,
  material  text NOT NULL,
  facts     jsonb NOT NULL,
  uses      text[] NOT NULL
);
CREATE TABLE brand.services (id text PRIMARY KEY, name text, detail text, condition_months integer);
CREATE TABLE brand.policies (id text PRIMARY KEY, title text, text text);
CREATE TABLE brand.care     (id text PRIMARY KEY, material text, topic text, advice text);
CREATE TABLE brand.events (
  id text PRIMARY KEY, name text, date date, weekday text, time text, location text, content text, status text
);
-- 语气、字数、禁用词等硬规则，供生成提示和规则校验共用
CREATE TABLE brand.rules (key text PRIMARY KEY, value jsonb NOT NULL);
-- 品牌文本块：品牌手册各节、沟通原则、场景范例、禁止表达、养护与政策条目
CREATE TABLE brand.chunks (
  id            text PRIMARY KEY,
  source        text NOT NULL,
  chunk_type    text NOT NULL,           -- voice / policy / scenario_example / taboo / care / service / positioning
  scenario_tags text[] NOT NULL,         -- 适用场景；'all' 表示通用
  title         text,
  text          text NOT NULL,
  embedding     vector(1536)
);
CREATE INDEX chunks_embedding_hnsw ON brand.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tags ON brand.chunks USING gin (scenario_tags);

-- ---------- crm ----------
CREATE TABLE crm.clients (
  id            text PRIMARY KEY,
  address       text NOT NULL,
  relationship  text,
  since         text,
  preferences   jsonb NOT NULL DEFAULT '{}',
  budget        integer,
  contact_prefs jsonb NOT NULL DEFAULT '{}',
  no_marketing  boolean NOT NULL DEFAULT false,
  notes         text
);
CREATE TABLE crm.purchases     (client_id text REFERENCES crm.clients(id), date date, sku text, color text, note text);
CREATE TABLE crm.key_dates     (client_id text REFERENCES crm.clients(id), type text, date text);
CREATE TABLE crm.sensitivities (client_id text REFERENCES crm.clients(id), type text, date date, note text);
CREATE TABLE crm.messages      (client_id text REFERENCES crm.clients(id), time timestamp, role text, text text);
CREATE INDEX messages_client ON crm.messages (client_id, time);
