--
-- PostgreSQL database dump
--

\restrict CzakjW58NaSjevoAOcGhF3AkIztayJymoGSnndq6cezIWCvrXY5eCMqsRWibGBn

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
-- Name: alert_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_notifications (
    event_id character varying(64) NOT NULL,
    transaction_id character varying(64) NOT NULL,
    risk_level character varying(16) NOT NULL,
    fraud_score double precision NOT NULL,
    rules_triggered json NOT NULL,
    status character varying(16) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_attempt_at timestamp without time zone,
    next_attempt_at timestamp without time zone,
    sent_at timestamp without time zone,
    last_error text
);


--
-- Name: processed_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processed_events (
    event_id character varying(64) NOT NULL,
    event_type character varying(100) NOT NULL,
    transaction_id character varying(64) NOT NULL,
    risk_level character varying(16) NOT NULL,
    processed_at timestamp without time zone NOT NULL
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: alert_notifications alert_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_notifications
    ADD CONSTRAINT alert_notifications_pkey PRIMARY KEY (event_id);


--
-- Name: processed_events processed_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processed_events
    ADD CONSTRAINT processed_events_pkey PRIMARY KEY (event_id);


--
-- Name: ix_alert_notifications_dispatch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_notifications_dispatch ON public.alert_notifications USING btree (status, next_attempt_at, created_at);


--
-- Name: ix_alert_notifications_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_notifications_transaction_id ON public.alert_notifications USING btree (transaction_id);


--
-- Name: ix_processed_events_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processed_events_transaction_id ON public.processed_events USING btree (transaction_id);


--
-- Name: alert_notifications alert_notifications_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_notifications
    ADD CONSTRAINT alert_notifications_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.processed_events(event_id);


--
-- PostgreSQL database dump complete
--

\unrestrict CzakjW58NaSjevoAOcGhF3AkIztayJymoGSnndq6cezIWCvrXY5eCMqsRWibGBn

