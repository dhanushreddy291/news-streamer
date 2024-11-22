import hashlib
import json
import os
import random
import time

import azure.cognitiveservices.speech as speechsdk
import boto3
import psycopg2
import requests
import openai
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from tqdm import tqdm

client = openai.OpenAI(
    api_key="fb054807-9c08-4abd-9183-61f43877bf4d",
    base_url="https://api.sambanova.ai/v1",
)

AWS_ENDPOINT_URL_S3 = os.environ.get("AWS_ENDPOINT_URL_S3")
AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME")
NEWS_URL = f"https://newsapi.org/v2/everything?q=crypto&sortBy=publishedAt&apiKey={os.environ.get('NEWS_API_KEY')}"

svc = boto3.client("s3", endpoint_url=AWS_ENDPOINT_URL_S3)

speech_key = os.environ.get("AZURE_SPEECH_KEY")
service_region = "centralus"

speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)


class NewsHeadlines(BaseModel):
    headline: str = Field(description="The headline of the news article")
    intro: str = Field(description="The introduction of the news article")
    brief: str = Field(description="A very long brief of the news article")
    reporterSpeech: str = Field(
        description="The speech given by the reporter, who is on the scene"
    )


class ImageUrl(BaseModel):
    url: str = Field(description="The url of the image")

newsTransitions = [
    "Oh my God, you won't believe this next story, folks!",
    "Breaking news, m'kay, here's what's happening now:",
    "Holy [bleep], we're getting reports of:",
    "In a shocking turn of events that's totally freaking everyone out:",
    "Sweet Jesus, this just in:",
    "Things are getting even crazier, folks! Check this out:",
    "Oh jeez, our next story is absolutely insane:",
    "You better sit down for this one, America:",
    "Holy hamburgers! Look what's happening now:",
    "This is giving me anxiety, but I have to report:",
    "For the love of all that's holy, get a load of this:",
    "Buckle up buckaroos, here comes another story:",
    "Good God almighty! Our sources are telling us:",
    "Well butter my biscuit, here's what's trending:",
    "Dear Lord, you won't believe what's happening now:",
]


def upload_file_for_streaming(filename, s3_client, bucket):
    # Extract the file extension to set proper content type
    file_extension = filename.split(".")[-1].lower()

    # Map of common audio extensions to MIME types
    content_types = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
    }

    # Get the appropriate content type, default to binary if unknown
    content_type = content_types.get(file_extension, "application/octet-stream")

    # Extra headers for the file
    extra_args = {
        "ContentType": content_type,
        "ContentDisposition": "inline",
        "ACL": "public-read",
    }

    # Upload the file with the extra arguments
    with open(filename, "rb") as f:
        s3_client.upload_fileobj(
            f, bucket, filename.split("/")[1], ExtraArgs=extra_args
        )

    # Return the URL
    url = f"{AWS_ENDPOINT_URL_S3}/{bucket}/{filename.split('/')[1]}"
    return url


def connect():
    return psycopg2.connect(os.environ.get("POSTGRES_URL"))


def generateAudio_ssml(text, voice_name, filename="", shout=True):
    """
    Generate audio from text with optional shouting emphasis using SSML.

    Args:
        text (str): Text to convert to speech
        voice_name (str): Name of the voice to use
        filename (str): Optional output directory/filename
        shout (bool): Whether to apply shouting emphasis using SSML

    Returns:
        str: Path to the generated audio file
    """
    # Create output directory if needed
    if filename != "":
        filename = f"{filename}/{hashlib.md5(text.encode()).hexdigest()}.wav"
    else:
        if not os.path.exists("audios"):
            os.makedirs("audios")
        filename = f"audios/{hashlib.md5(text.encode()).hexdigest()}.wav"

    print(filename)
    speech_config.speech_synthesis_voice_name = voice_name
    audio_config = speechsdk.audio.AudioOutputConfig(filename=filename)
    speech_synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )

    if shout:
        # Wrap the text in SSML with increased volume, rate, and emphasis
        ssml_text = f"""
        <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" 
               xmlns:mstts="http://www.w3.org/2001/mstts" 
               xml:lang="en-US">
            <voice name="{voice_name}">
                <mstts:express-as style="shouting">
                    <prosody rate="25%" pitch="+5%">
                        {text}
                    </prosody>
                </mstts:express-as>
            </voice>
        </speak>
        """
        result = speech_synthesizer.speak_ssml_async(ssml_text).get()
    else:
        result = speech_synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f"Audio saved to {filename}")

        # Upload the file to S3
        upload_file_for_streaming(filename, svc, AWS_BUCKET_NAME)

        # # Delete the file after uploading
        os.remove(filename)

        return filename.split("/")[1]
    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        raise Exception(
            f"Speech synthesis canceled: {cancellation_details.reason}. Details: {cancellation_details.error_details}"
        )

def get_article_as_markdown(urlOfArticle):
    # https://r.jina.ai/https://www.space.com/space-exploration/international-space-station/spacex-dragon-fires-thrusters-to-boost-iss-orbit-for-the-1st-time
    print(f"Fetching article: {urlOfArticle}")
    response = requests.get(f"https://r.jina.ai/{urlOfArticle}")
    if response.status_code == 200:
        return response.text
    else:
        print("Error fetching article")
        return ""

def check_if_scrapping_was_successfull(title, article_text):
    response = client.chat.completions.create(
        model="Meta-Llama-3.1-70B-Instruct",
        messages=[
            {
                "role": "system",
                "content": "You are given text that was scrapped from a website. You need to check if the scrapping was successful and the text is valid and relevant to the article. Return your response as json. The json should contain a key 'valid' which should be a boolean value.",
            },
            {
                "role": "user",
                "content": f"The title of the article is: {title}\n\nThe article text from scrapping is: {article_text}",
            },
        ],
        response_format={
            "type": "json_object"
        },
    )
    basic_response = response.choices[0].message.content
    return json.loads(basic_response)["valid"]


def generate_article_content(title, article_text):
    system_prompt = """
    You are a news anchor in the style of South Park's news reporting. When delivering news, your style should be:

    1. **Exaggerated and Dramatic:** Use over-the-top reactions like South Park's news anchors, with dramatic tones and absurd commentary, yet maintaining the facade of a serious news persona.
    2. **Catchphrases:** Incorporate lines such as "Breaking news, m'kay!" and "This just in!" to capture the exaggerated South Park style.
    3. **Blunt Social Commentary:** Blend factual news with satirical humor, keeping the actual content accurate but delivering it in a comedic, exaggerated fashion.
    4. **Ongoing Chaos:** Occasionally reference a "panic" or "chaos" in a mock-serious way, even for mundane events.
    5. **Sarcastic Wrap-Up:** Conclude with sarcastic remarks that humorously critique the "state of affairs."
    6. The langauge of the news should always be in English.

    **News Structure:**
    - There are two characters in this news report: the news anchor and the on-the-scene reporter. No other characters are present.
    - The **headline**, **intro**, and **brief** are delivered by the news anchor, named Tom in this exaggerated tone.
    - The **reporterSpeech** is spoken by Bob, an equally dramatic on-the-scene reporter, who provides a live update from the event location, adding his own exaggerated flair to the situation. He begins with a Thanks, and then gives a dramatic report and ends with a "Back to you, Tom!"

    Your output should be a JSON object containing the following:
    - **headline**: The title of the news story.
    - **intro**: A short introductory sentence.
    - **brief**: A brief summary of the article, also containing the nmessage where news anchor asks Bob to give a report.
    - **reporterSpeech**: Bob's dramatic, over-the-top live report from the scene.

    Maintain the comedic South Park style throughout, with a clear distinction between the news anchor's lines and Bob's on-the-scene update.

    You need to return a json consisting of headline, intro, brief and reporterSpeech.
    """
    response = client.chat.completions.create(
        model="Meta-Llama-3.1-70B-Instruct",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"The title of the article is: {title}\n\nThe article text is: {article_text}",
            },
        ],
        response_format={
            "type": "json_object",
            "schema": NewsHeadlines.model_json_schema(),
        },
    )
    basic_response = response.choices[0].message.content
    print("--------------------", basic_response)
    return json.loads(basic_response)


def getNews():
    NEWS_URL = f"https://newsapi.org/v2/top-headlines?category=science&apiKey={os.getenv('NEWS_API_KEY')}"
    response = requests.get(NEWS_URL)
    if response.status_code == 200:
        news = response.json()
        return news["articles"]
    else:
        print("Error fetching news")
        return []

if __name__ == "__main__":
    conn = connect()
    cursor = conn.cursor()

    # Check if the cron job is already running
    cursor.execute("SELECT is_running FROM is_cron_job_running")

    rows = cursor.fetchone()
    if not rows:
        cursor.execute("INSERT INTO is_cron_job_running (is_running) VALUES (false)")
        conn.commit()

    is_running = False

    if is_running:
        print("Cron job is already running")
    else:
        print("Starting cron job, updating is_running to true")
        cursor.execute("UPDATE is_cron_job_running SET is_running = true WHERE true")
        conn.commit()
        print("Updated is_running to true")

        news = getNews()

        for article in news:
            try:
                print("--------------------")
                # Check if the article already exists in the database
                cursor.execute(
                    "SELECT id FROM news WHERE title = %s", (article["title"],)
                )
                if cursor.fetchone():
                    print(f"Article already exists: {article['title']}")
                    continue

                print(f"Scrapping the article: {article['title']}...")

                [title, description, url, urlToImage] = [
                    article["title"],
                    article["description"],
                    article["url"],
                    article["urlToImage"],
                ]

                # Get the article as markdown text
                article_text = get_article_as_markdown(url)

                if article_text != "":
                    print(f"Checking if article is valid: {title}")
                    # Check if valid article using AI
                    is_valid = check_if_scrapping_was_successfull(title, article_text)

                    if is_valid:
                        print(f"Article scrapped is valid: {title}")
                        article_content: NewsHeadlines = generate_article_content(
                            title, article_text
                        )

                        print(f"Article summary: {article_content['brief']}")

                        news_headline = random.choice(newsTransitions)
                        ai_summary = f"{news_headline} {article_content['headline']}. {article_content['intro']}. {article_content['brief']}".strip()

                        # Generate audio
                        # audio_path = generate_audio_elevenlabs(ai_summary)
                        audio_path = generateAudio_ssml(ai_summary, "en-US-GuyNeural")
                        reporter_speech = article_content["reporterSpeech"]
                        audio_path2 = generateAudio_ssml(
                            reporter_speech, "en-US-TonyNeural"
                        )

                        # Insert article into database
                        cursor.execute(
                            "INSERT INTO news (title, description, ai_summary_of_description, urlOfArticle, audio_path_stored, audio_path_reporter, urlToImage, headline, json_content) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                title,
                                description,
                                ai_summary,
                                url,
                                audio_path,
                                audio_path2,
                                urlToImage,
                                article_content["headline"],
                                json.dumps(article_content),
                            ),
                        )

                        conn.commit()
                        print(f"Article inserted into database: {title}")

                    else:
                        print(f"Article scrapped was not valid: {title}")

                else:
                    print(
                        f"Error fetching article content: '{title}', mostly due to scrapping issue"
                    )

            except Exception as e:
                print(f"Error: {e}")

            finally:
                print("--------------------")

        print("Cron job finished, updating is_running to false")

        cursor.execute("UPDATE is_cron_job_running SET is_running = false WHERE true")
        conn.commit()

        print("Cron job finished, updated is_running to false")

    cursor.close()
    conn.close()

    print("Done")
