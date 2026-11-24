# Mail assistant skeleton

`POST /api/mail/assistant` takes a prompt and returns `{"answer": "..."}`. It
uses the current Mini-Mailroom session. The assistant can summarize available
mail, suggest labels, and return reply drafts for review. Each request is a
single turn. Drafts and label suggestions are plain text in the API response.

The FastAPI route checks the session and request origin and uses the shared
DynamoDB rate limiter in production. The Groq agent discovers tools through an
in-process MCP client and server. The MCP tools use the signed-in user's
existing Gmail connection to list up to eight recent inbox messages, read the
available plain text of a message, and list up to 40 user-created labels.
The turn has a 25-second model and tool deadline and allows up to four tool calls.

Enabling the assistant allows Groq to receive prompts and the mailbox data
retrieved during a run, such as sender, subject, snippet, label names, and up to
6,000 characters of plain text per message. Gmail credentials, HTML, inline
images, and attachments stay outside the model request. Use a test mailbox for
the first smoke test and review your Groq project's data settings before
enabling the assistant for real mail.

## Run locally

Set `GROQ_API_KEY` in the private `server/.env` file, then use the normal
Mini-Mailroom development setup and Gmail sign-in. `GROQ_MODEL` defaults to
`openai/gpt-oss-20b`. If you override it, choose a Groq model that supports
local tool calling. [Groq local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling)

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
   settings are `GROQ_MODEL` and `ASSISTANT_REQUESTS_PER_MINUTE` (default
   `5` per signed-in user). The key itself belongs in Secrets Manager.
4. In **Lambda → Configuration → General configuration**, set the timeout to
   **60 seconds** for the initial test. This leaves time for session and secret
   lookups as well as the 25-second agent deadline. The function needs outbound
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

Open the app at its Function URL, sign in to the test mailbox, and run this in
the browser developer console. The current session cookie goes with the same-origin
request. Run the same check on `http://localhost:5173` for local development.

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
HTTP 401 indicates a missing or expired session. HTTP 503 with
`Mail assistant is disabled.` indicates a missing key source. For
`Mail assistant temporarily unavailable.`, check the secret ARN, the
`GROQ_API_KEY` field, and the execution role permissions. For HTTP 502, check
the model name, Groq project key, and outbound connectivity. Lambda logs the
error type for failed Groq requests. HTTP 504 indicates an assistant timeout.

## Code entry points

| File | Responsibility |
| --- | --- |
| `server/app/assistant/router.py` | Request validation, auth, quota, and HTTP response |
| `server/app/assistant/agent.py` | System instructions, Groq requests, and MCP tool-call loop |
| `server/app/assistant/mail_tools.py` | MCP tool definitions and mailbox data passed to the model |
| `server/tests/test_assistant.py` | HTTP-to-MCP tests with fake provider I/O |
