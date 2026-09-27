"""Add first person pronoun rule"""
with open("app/ai/prompt.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add to both TASK sections (self role)
old_self = '- 1文ごとに改行して読みやすくする\n- 同じ話題が'
new_self = '- 1文ごとに改行して読みやすくする\n- 一人称は必ず「僕」を使う\n- 同じ話題が'

if old_self in content:
    content = content.replace(old_self, new_self, 1)

with open("app/ai/prompt.py", "w", encoding="utf-8") as f:
    f.write(content)

print("OK")
