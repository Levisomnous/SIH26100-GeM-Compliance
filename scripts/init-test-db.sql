-- Runs once, the first time the compose Postgres volume is created.
-- Scratch database used only by the test suite (it is wiped on every test).
CREATE DATABASE gem_test;
