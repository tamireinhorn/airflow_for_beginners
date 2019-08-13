import logging

import requests
from airflow import DAG
from airflow.hooks.postgres_hook import PostgresHook
from airflow.operators.bash_operator import BashOperator
from datetime import datetime, timedelta

from airflow.operators.postgres_operator import PostgresOperator
from airflow.operators.python_operator import PythonOperator

from airflow.models import Variable

# TODO: remove this from here and import from src (leave only DAG definitions)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ApiError(Exception):
    def __init__(self, *args):
        Exception.__init__(self, *args)


def parse_question(question: dict) -> dict:
    """ Returns parsed question from Stack Overflow API """
    creation_date = datetime.fromtimestamp(question["creation_date"])
    return {
        "question_id": question["question_id"],
        "title": question["title"],
        "is_answered": question["is_answered"],
        "link": question["link"],
        "owner_reputation": question["owner"]["reputation"],
        "owner_accept_rate": question["owner"].get("accept_rate", 0),
        "score": question["score"],
        "tags": question["tags"],
        "creation_date": creation_date,
    }


def call_stack_overflow_api():
    """ Get first 100 questions created in the last 24 hours sorted by user votes. """
    stackoverflow_question_url = Variable.get("STACKOVERFLOW_QUESTION_URL")
    today = datetime.now()
    three_days_ago = today - timedelta(days=3)
    two_days_ago = today - timedelta(days=2)
    tag = "pandas"
    payload = {
        "fromdate": int(datetime.timestamp(three_days_ago)),
        "todate": int(datetime.timestamp(two_days_ago)),
        "sort": "votes",
        "site": "stackoverflow",
        "order": "desc",
        "tagged": tag,
        "pagesize": 100,
        "client_id": Variable.get("STACKOVERFLOW_CLIENT_ID"),
        "client_secret": Variable.get("STACKOVERFLOW_CLIENT_SECRET"),
        "key": Variable.get("STACKOVERFLOW_KEY"),
    }
    response = requests.get(stackoverflow_question_url, params=payload)
    if response.status_code != 200:
        raise ApiError(
            f"Cannot fetch questions: {response.status_code} \n {response.json()}"
        )
    for question in response.json().get("items", []):
        yield parse_question(question)


def add_question():
    """
    Add a new question to the database
    """
    insert_question_query = (
        "INSERT INTO public.questions "
        "(question_id, title, is_answered, link, "
        "owner_reputation, owner_accept_rate, score, "
        "tags, creation_date) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);"
    )

    rows = call_stack_overflow_api()
    for row in rows:
        pg_hook = PostgresHook(postgres_conn_id="postgres_so")
        row = tuple(row.values())
        pg_hook.run(insert_question_query, parameters=row)


def filter_questions():
    query = "SELECT title, is_answered, link, score, tags, question_id, owner_reputation, owner_accept_rate FROM public.questions limit 2;"
    pg_hook = PostgresHook(postgres_conn_id="postgres_so").get_conn()
    src_cursor = pg_hook.cursor("serverCursor")
    src_cursor.execute(query)
    rows = src_cursor.fetchall()
    columns = (
        "title",
        "is_answered",
        "link",
        "score",
        "tags",
        "question_id",
        "owner_reputation",
        "owner_accept_rate",
    )
    results = []
    for row in rows:
        record = dict(zip(columns, record))
        results.append((row))

    print(results)
    src_cursor.close()
    pg_hook.close()


default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2019, 6, 1),
    "email": ["airflow@varya.io"],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    "tutorial", default_args=default_args, schedule_interval=timedelta(days=1)
) as dag:
    t1 = PostgresOperator(
        task_id="truncate_questions_table",
        postgres_conn_id="postgres_so",
        sql="TRUNCATE table public.questions",
        database="stack_overflow",
        dag=dag,
    )

    t2 = PythonOperator(
        task_id="insert_questions_into_db", python_callable=add_question, dag=dag
    )
    t3 = PythonOperator(
        task_id="read_questions_from_db", python_callable=filter_questions, dag=dag
    )


t1 >> t2 >> t3
