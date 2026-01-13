"""Concurrency tests for Magpie artifact storage.

These tests verify that concurrent operations don't cause data corruption
or race conditions. They use asyncio.gather() to simulate multiple
concurrent requests.
"""
