# Mail assistant

Open **Assistant** from the mailbox navigation and describe the task in your
own words. Try one of these prompts.

- Summarize my last five inbox emails and highlight deadlines and notices.
- Read my newest eight emails and apply any relevant Gmail labels.
- Write a quick thank-you to Lee at lee@example.com and send it.

The assistant completes the steps in one turn. Asking for a draft or label
suggestions opens review controls. **Review and send** lets you edit a draft.
You can also ask the assistant to send the draft as written. **Use open email**
includes the selected message when you ask about it.

The agent gets one short chat exchange and loads mail, labels, or the current
draft as needed. Inbox reads use brief body excerpts, with a smaller budget
when loading labels for classification. A batch that completes the request
can return Gmail's receipt directly.
Before a Gmail write, a separate check uses only the latest user prompt to
verify that it requests the action. The Gmail connection belongs to the current
Mini-Mailroom session. Confirmed updates are returned to the chat.

## Run locally

Set `GROQ_API_KEY` in the private `server/.env` file, then use the normal
Mini-Mailroom development setup and Gmail sign-in. `GROQ_MODEL` defaults to
`openai/gpt-oss-120b`, with `openai/gpt-oss-20b` as the default fallback.
If you set `GROQ_MODEL=openai/gpt-oss-20b`, the default fallback is 120B.
Set `GROQ_FALLBACK_MODEL` to override the fallback or set it to an empty value
to disable it. Both models need local tool calling and JSON mode. [Groq local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling)

## Enable it in AWS

Apply these changes to each existing Mini-Mailroom environment where you want
the assistant enabled.

1. In **AWS Secrets Manager**, create a secret in the Lambda function's region.
   Choose **Other type of secret** and add a key named `GROQ_API_KEY` with the
   Groq API key as its value. Use a distinct secret per environment, such as
   `mini-mailroom/dev/groq-api-key` and `mini-mailroom/prod/groq-api-key`.
   Copy the full secret ARN, including the generated suffix.
2. In **IAM**, add the following policy to the **Lambda execution role**,
   substituting the actual secret ARN. A same-account secret using the default
   `aws/secretsmanager` key only needs this permission. For a customer-managed
   key, also grant `kms:Decrypt` on that key and ensure the key policy allows
   the role to use it through Secrets Manager. This differs from the existing
   Gmail credential encryption permission, which uses a Gmail-specific
   encryption context.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "secretsmanager:GetSecretValue",
         "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:mini-mailroom/ENV/groq-api-key-SUFFIX"
       }
     ]
   }
   ```

3. In **Lambda → Configuration → Environment variables**, add
   `GROQ_API_KEY_SECRET_ARN` and preserve the existing variables. Optional
   settings are `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, and
   `ASSISTANT_REQUESTS_PER_MINUTE` (default `5` per signed-in user). The
   primary and fallback use the same Groq key. Enable both models in the
   Groq project if it restricts model access.
4. In **Lambda → Configuration → General configuration**, set the timeout to
   **60 seconds** for the initial test. This leaves time for session and secret
   lookups as well as the agent's request deadline. The function needs outbound
   HTTPS to `api.groq.com`. The existing Lambda deployment outside a VPC has
   internet access. If your function is VPC-attached, provide internet egress
   through a NAT or equivalent route.
5. Run the existing **CodeBuild** project on the revision containing these
   changes. After it pushes the new `lambda-<commit>-<build>` tag to **ECR**,
   update the function's container image to that tag. CodeBuild installs the
   locked dependencies and runs the backend checks. The key is read lazily by
   Lambda at runtime.

The existing Function URL, Gmail OAuth scopes, DynamoDB table, and container
build are used by the assistant. The MCP server runs inside the app container.
Remove `GROQ_API_KEY_SECRET_ARN` to disable the endpoint in that environment.
Warm instances cache the key for five minutes. The first assistant request
after the cache expires reads the current secret value.

## Verify the deployment

Open the app at its Function URL and sign in to a test mailbox. Ask the
assistant to summarize the deadlines in your latest five emails and then try a
separate request to read and label the same batch. Check the labels in Gmail.
Ask it to write and send a short test email to an address you control, then
verify the result in Sent.

To check the API directly, run this in the developer console on the signed-in
app page. The current session cookie goes with the same-origin request. The
same check works on `http://localhost:5173` for local development.

```js
const response = await fetch("/api/mail/assistant", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Mailroom-Request": "1",
  },
  body: JSON.stringify({ prompt: "Summarize the latest two messages in my inbox." }),
});
console.log(response.status, await response.json());
```

Expect HTTP 200 and an `answer` with the available mail summarized.
The request can include an open `message_id`, a current `draft`, and recent
`history` entries with `prompt` and `answer` fields. The response can include
review `actions`, confirmed `label_updates`, and the `draft_delivery` status.
HTTP 401 indicates a missing or expired session. HTTP 503 with
`Mail assistant is disabled.` indicates a missing key source. For
`Mail assistant temporarily unavailable.`, check the secret ARN, the
`GROQ_API_KEY` field, and the execution role permissions. HTTP 429 with
`Groq's rate limit was reached` means the Groq organization hit a quota. Check
the organization's model limits, including tokens per minute. A Groq `429`
switches the current turn to the fallback model at the failed call. If that
model also returns `429`, the chat keeps your prompt and shows the retry delay
when provided. [Groq rate limits](https://console.groq.com/docs/rate-limits)

For HTTP 502, check the provider status and error code in the Lambda's
CloudWatch logs. For example, `tool_use_failed` means Groq rejected a generated
tool call. HTTP 504 indicates an assistant timeout.

## Code entry points

| File | Responsibility |
| --- | --- |
| `client/src/features/mail/AssistantChat.tsx` | Chat, open-email context, and review controls |
| `server/app/assistant/router.py` | Request validation, auth, quota, and HTTP response |
| `server/app/assistant/agent.py` | System instructions, Groq requests, and MCP tool-call loop |
| `server/app/assistant/mail_tools.py` | MCP tool definitions and mailbox data passed to the model |
| `server/tests/test_assistant.py` | HTTP-to-MCP tests with fake provider I/O |
