#!/usr/bin/env python3
import shutil
from pathlib import Path

filepath = Path("/home/pudding/Projects/VelaNova/orchestrator/voice_loop.py")
backup = filepath.with_suffix('.py.backup-logging-script')
shutil.copy(filepath, backup)

lines = filepath.read_text().splitlines(keepends=True)

# Find the for loop line
for_loop_idx = None
for i, line in enumerate(lines):
    if 'for content, emb_bytes, ts in cursor:' in line:
        for_loop_idx = i
        break

if for_loop_idx is None:
    print("ERROR: Could not find cursor loop")
    exit(1)

# Insert row_count = 0 before the for loop
indent = '            '
lines.insert(for_loop_idx, f'{indent}row_count = 0\n')
for_loop_idx += 1  # Adjust index after insertion

# Add counter and logging as FIRST line inside for loop
indent_inner = '                '
log_line = f'{indent_inner}row_count += 1\n{indent_inner}self.logger.debug("semantic_search_row %s", json.dumps({{"row": row_count, "has_emb": emb_bytes is not None, "emb_len": len(emb_bytes) if emb_bytes else 0, "content_preview": content[:50]}}))\n'
lines.insert(for_loop_idx + 1, log_line)

# Find and fix exception handler
for i in range(for_loop_idx, len(lines)):
    if 'except Exception:' in lines[i] and 'continue' in lines[i+1]:
        lines[i] = lines[i].replace('except Exception:', 'except Exception as e:')
        lines[i+1] = f'{indent_inner}        self.logger.error("semantic_search_row_failed %s", json.dumps({{"row": row_count, "error": str(e), "type": type(e).__name__, "content": content[:50]}}))\n{lines[i+1]}'
        break

# Add completion logging before conn.close()
for i in range(for_loop_idx, len(lines)):
    if 'conn.close()' in lines[i]:
        lines.insert(i, f'{indent}self.logger.debug("semantic_search_complete %s", json.dumps({{"rows_fetched": row_count, "results_count": len(results), "excluded_count": len(excluded)}}))\n')
        break

filepath.write_text(''.join(lines))
print(f"✅ Logging added. Backup: {backup}")
