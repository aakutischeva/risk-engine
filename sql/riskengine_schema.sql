--
-- PostgreSQL database dump
--

\restrict jSICOFh0edzd7BLIUgEU1bouJR0WgoQgdBApnUfb08DUZUgBqMkCyyhBHExKNBz

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    alert_id character varying(64) NOT NULL,
    transaction_id character varying(64) NOT NULL,
    risk_level character varying(16) NOT NULL,
    fraud_score double precision NOT NULL,
    rules_triggered json NOT NULL,
    status character varying(32) NOT NULL,
    notes text,
    created_at timestamp without time zone NOT NULL,
    resolved_at timestamp without time zone
);


--
-- Name: outbox_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_events (
    event_id character varying(64) NOT NULL,
    event_type character varying(100) NOT NULL,
    aggregate_id character varying(64) NOT NULL,
    payload json NOT NULL,
    created_at timestamp without time zone NOT NULL,
    published_at timestamp without time zone,
    attempts integer NOT NULL,
    last_error text
);


--
-- Name: transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transactions (
    transaction_id character varying(64) NOT NULL,
    amount double precision NOT NULL,
    payer_key character varying(256) NOT NULL,
    receiver_key character varying(256) NOT NULL,
    transaction_type character varying(16) NOT NULL,
    fraud_score double precision NOT NULL,
    risk_level character varying(16) NOT NULL,
    risk_details json NOT NULL,
    metadata json,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (alert_id);


--
-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (event_id);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (transaction_id);


--
-- Name: outbox_events uq_outbox_event_type_aggregate; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT uq_outbox_event_type_aggregate UNIQUE (event_type, aggregate_id);


--
-- Name: ix_alerts_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_alert_id ON public.alerts USING btree (alert_id);


--
-- Name: ix_alerts_risk_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_risk_level ON public.alerts USING btree (risk_level);


--
-- Name: ix_alerts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_status ON public.alerts USING btree (status);


--
-- Name: ix_alerts_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_transaction_id ON public.alerts USING btree (transaction_id);


--
-- Name: ix_outbox_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_pending ON public.outbox_events USING btree (published_at, created_at);


--
-- Name: ix_transactions_payer_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_payer_created ON public.transactions USING btree (payer_key, created_at);


--
-- Name: ix_transactions_payer_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_payer_key ON public.transactions USING btree (payer_key);


--
-- Name: ix_transactions_risk_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_risk_created ON public.transactions USING btree (risk_level, created_at);


--
-- Name: ix_transactions_risk_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_risk_level ON public.transactions USING btree (risk_level);


--
-- Name: ix_transactions_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_transaction_id ON public.transactions USING btree (transaction_id);


--
-- PostgreSQL database dump complete
--

\unrestrict jSICOFh0edzd7BLIUgEU1bouJR0WgoQgdBApnUfb08DUZUgBqMkCyyhBHExKNBz

