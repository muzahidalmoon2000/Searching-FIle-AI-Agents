from flask import Flask, render_template, request, redirect, session, jsonify
from dotenv import load_dotenv
import os
import msal
from auth.msal_auth import get_token_from_cache
from graph_api import search_all_files, check_file_access, send_notification_email
from openai_api import detect_intent_and_extract, answer_general_query

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("CLIENT_SECRET")

@app.route("/")
def home():
    if not session.get("user_email"):
        return redirect("/login")
    session['stage'] = 'start'
    session['found_files'] = []
    return render_template("chat.html")

@app.route("/login")
def login():
    msal_app = msal.ConfidentialClientApplication(
        os.getenv("CLIENT_ID"),
        authority=os.getenv("AUTHORITY"),
        client_credential=os.getenv("CLIENT_SECRET")
    )
    auth_url = msal_app.get_authorization_request_url(
        scopes=os.getenv("SCOPE").split(),
        redirect_uri=os.getenv("REDIRECT_URI")
    )
    return redirect(auth_url)

@app.route("/getAToken")
def authorized():
    code = request.args.get("code")
    if not code:
        return "Authorization failed", 400
    msal_app = msal.ConfidentialClientApplication(
        os.getenv("CLIENT_ID"),
        authority=os.getenv("AUTHORITY"),
        client_credential=os.getenv("CLIENT_SECRET")
    )
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=os.getenv("SCOPE").split(),
        redirect_uri=os.getenv("REDIRECT_URI")
    )
    session["token"] = result["access_token"]
    session["user_email"] = result.get("id_token_claims", {}).get("preferred_username")
    return redirect("/")

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")
    token = session.get("token")
    user_email = session.get("user_email")
    stage = session.get("stage", "start")

    if not token or not user_email:
        return jsonify(response="❌ You are not logged in.")

    if stage == "start":
        session["stage"] = "awaiting_query"
        return jsonify(response="Hi there! 👋\nWhat file are you looking for today or how can I help?")

    elif stage == "awaiting_query":
        gpt_result = detect_intent_and_extract(user_input)
        intent = gpt_result.get("intent")
        query = gpt_result.get("data")
        print(f"🔍 GPT intent: {intent} | query: {query}")

        if intent == "general_response":
            gpt_reply = answer_general_query(user_input)
            return jsonify(response=gpt_reply)

        elif intent == "file_search" and query:
            session["last_query"] = query
            files = search_all_files(token, query)
            session["found_files"] = files

            if not files:
                return jsonify(response="📁 No files found for your request. Try being more specific.")

            exact_matches = [f for f in files if f["name"].lower() == query.lower()]
            if exact_matches:
                file = exact_matches[0]
                has_access = check_file_access(token, file['id'], user_email, file.get("parentReference", {}).get("siteId"))
                session["stage"] = "awaiting_query"  # Resume flow after reply
                if has_access:
                    send_notification_email(token, user_email, file['name'], file['webUrl'])
                    return jsonify(response=f"✅ You have access! Here’s your file link: {file['webUrl']}\n📧 Sent to your email: {user_email}\n\n💬 Do you need anything else? Or are you fine for now?")
                else:
                    return jsonify(response="❌ You don’t have access to this file.")
            else:
                session["stage"] = "awaiting_selection"
                file_list = "\n".join([f"{i+1}. {f['name']}" for i, f in enumerate(files[:5])])
                return jsonify(response=f"Here are some files I found:\n{file_list}\n\nPlease reply with the number of the file you want.")

        else:
            return jsonify(response="⚠️ I couldn’t understand your request. Please rephrase or provide more detail.")

    elif stage == "awaiting_selection":
        try:
            idx = int(user_input.strip()) - 1
            files = session.get("found_files", [])
            if idx < 0 or idx >= len(files):
                return jsonify(response="❌ Invalid selection. Please reply with a valid number from the list.")
            file = files[idx]
            has_access = check_file_access(token, file['id'], user_email, file.get("parentReference", {}).get("siteId"))
            session["stage"] = "awaiting_query"
            if has_access:
                send_notification_email(token, user_email, file['name'], file['webUrl'])
                return jsonify(response=f"✅ You have access! Here’s your file link: {file['webUrl']}\n📧 Sent to your email: {user_email}\n\n💬 Do you need anything else? Or are you fine for now?")
            else:
                return jsonify(response="❌ You don’t have access to this file or it no longer exists.")
        except ValueError:
            return jsonify(response="❌ Please enter a valid number from the list.")

    return jsonify(response="⚠️ Something went wrong. Please try again.")




if __name__ == "__main__":
    app.run(debug=True)