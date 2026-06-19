"""User-defined module nodes.

Each public function follows the node contract::

    async def fn(inputs: dict, state: dict, config: dict) -> dict | str: ...

Return a string to write under the node's ``output_key``, or a dict to write as
a whole (under ``output_key``) or merged into state (with ``merge_output: true``).
Functions may be sync or async; sync functions run in a thread pool.
"""
