"""Skill taxonomy: canonical skills, their aliases, and how they relate.

Everything that compares a job with a candidate's evidence goes through this module, so that "Postgres",
"PostgreSQL" and a `psycopg` dependency are recognised as the same skill, and so that related skills
(e.g. GCP for an AWS requirement) can be reported as *partial* evidence rather than a match.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class SkillDef:
    key: str
    name: str
    category: str
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    # Aliases matched case-sensitively and only in list-like contexts (e.g. "Go", "R", "C").
    strict_aliases: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()  # broader skills this one demonstrates (FastAPI -> REST APIs)


_S: list[SkillDef] = []


def _add(key, name, category, aliases=(), related=(), strict=(), parents=()):
    _S.append(SkillDef(key, name, category, tuple(aliases), tuple(related), tuple(strict), tuple(parents)))


# --- Languages -----------------------------------------------------------------------------------
_add("python", "Python", "language", ["python3", "python 3"])
_add("java", "Java", "language", ["java 8", "java 11", "java 17", "core java", "j2ee", "java ee"], ["kotlin"])
_add("javascript", "JavaScript", "language", ["js", "es6", "ecmascript", "vanilla js"], ["typescript"])
_add("typescript", "TypeScript", "language", ["ts"], ["javascript"])
_add("cpp", "C++", "language", ["c++", "cpp", "c++11", "c++14", "c++17", "c++20"], ["c"])
_add("c", "C", "language", ["c programming", "ansi c"], ["cpp"], strict=["C"])
_add("csharp", "C#", "language", ["c#", "csharp", "c sharp"], ["dotnet", "java"])
_add("go", "Go", "language", ["golang"], strict=["Go"])
_add("rust", "Rust", "language", [], ["cpp"])
_add("kotlin", "Kotlin", "language", [], ["java"])
_add("swift", "Swift", "language", [], ["objective_c"])
_add("objective_c", "Objective-C", "language", ["objective-c", "objective c"])
_add("ruby", "Ruby", "language")
_add("php", "PHP", "language")
_add("scala", "Scala", "language", [], ["java"])
_add("r", "R", "language", ["r programming", "rstudio"], ["python"], strict=["R"])
_add("matlab", "MATLAB", "language")
_add("sql", "SQL", "language", ["t-sql", "pl/sql", "plsql", "structured query language", "sql queries"])
_add("bash", "Bash", "language", ["shell scripting", "shell script", "bash scripting", "unix shell"])
_add("html", "HTML", "language", ["html5"])
_add("css", "CSS", "language", ["css3", "scss", "sass"])
_add("dart", "Dart", "language", [], ["flutter"])
_add("abap", "ABAP", "language", ["sap abap"])
_add("solidity", "Solidity", "language")

# --- Frontend ------------------------------------------------------------------------------------
_add("react", "React", "frontend", ["react.js", "reactjs", "react js"], ["nextjs", "vue", "angular"])
_add("nextjs", "Next.js", "frontend", ["next.js", "nextjs", "next js"], ["react"], parents=["react"])
_add("vue", "Vue.js", "frontend", ["vue", "vuejs", "vue.js", "vue 3"], ["react", "angular"])
_add("angular", "Angular", "frontend", ["angularjs", "angular.js"], ["react", "vue"])
_add("svelte", "Svelte", "frontend", ["sveltekit"], ["react"])
_add("redux", "Redux", "frontend", ["redux toolkit"], ["react"])
_add("tailwind", "Tailwind CSS", "frontend", ["tailwindcss", "tailwind"], ["css"])
_add("bootstrap", "Bootstrap", "frontend", [], ["css"])
_add("vite", "Vite", "frontend", [], ["webpack"])
_add("webpack", "Webpack", "frontend", [], ["vite"])
_add("threejs", "Three.js", "frontend", ["three.js", "threejs", "webgl"])
_add("d3", "D3.js", "frontend", ["d3.js", "d3js"], ["data_visualization"])
_add("jquery", "jQuery", "frontend")
_add("sapui5", "SAPUI5", "frontend", ["sap ui5", "ui5", "openui5", "sap fiori", "fiori"])
_add("react_native", "React Native", "mobile", ["react-native"], ["react", "flutter"])
_add("flutter", "Flutter", "mobile", [], ["react_native"])
_add("android", "Android", "mobile", ["android development", "android sdk"], ["kotlin"])
_add("ios", "iOS", "mobile", ["ios development"], ["swift"])

# --- Backend -------------------------------------------------------------------------------------
_add("nodejs", "Node.js", "backend", ["node.js", "nodejs", "node js", "node"], ["express"])
_add("express", "Express.js", "backend", ["express.js", "expressjs", "express js"], ["nodejs", "nestjs"],
     parents=["nodejs", "rest_api"])
_add("nestjs", "NestJS", "backend", ["nest.js"], ["express"], parents=["nodejs"])
_add("fastapi", "FastAPI", "backend", ["fast api"], ["flask", "django"], parents=["python", "rest_api"])
_add("django", "Django", "backend", ["django rest framework", "drf"], ["flask", "fastapi"], parents=["python"])
_add("flask", "Flask", "backend", [], ["fastapi", "django"], parents=["python"])
_add("spring_boot", "Spring Boot", "backend", ["spring boot", "springboot", "spring framework", "spring mvc", "spring"],
     ["java"], parents=["java"])
_add("hibernate", "Hibernate", "backend", ["jpa", "spring data jpa"], ["spring_boot"])
_add("dotnet", ".NET", "backend", [".net", "asp.net", ".net core", "asp.net core", "dotnet"], ["csharp"])
_add("rails", "Ruby on Rails", "backend", ["rails", "ruby on rails", "ror"], ["ruby"])
_add("laravel", "Laravel", "backend", [], ["php"])
_add("graphql", "GraphQL", "backend", ["apollo"], ["rest_api"])
_add("grpc", "gRPC", "backend", ["protobuf", "protocol buffers"], ["rest_api"])
_add("websockets", "WebSockets", "backend", ["websocket", "socket.io", "socketio"])
_add("sap_cap", "SAP CAP", "backend", ["cloud application programming model", "sap cloud application programming"],
     ["nodejs"], strict=["CAP"])
_add("sap_btp", "SAP BTP", "cloud", ["btp", "sap business technology platform", "sap cloud platform"])

# --- Databases -----------------------------------------------------------------------------------
_add("postgresql", "PostgreSQL", "database", ["postgres", "postgresql", "psql", "pgsql"], ["mysql", "sql"],
     parents=["sql"])
_add("mysql", "MySQL", "database", ["mariadb"], ["postgresql", "sql"], parents=["sql"])
_add("sqlite", "SQLite", "database", ["sqlite3"], ["postgresql", "mysql"], parents=["sql"])
_add("oracle_db", "Oracle Database", "database", ["oracle db", "oracle database", "oracle sql", "pl/sql"], ["sql"],
     parents=["sql"])
_add("sql_server", "SQL Server", "database", ["mssql", "ms sql", "microsoft sql server", "sql server"], ["sql"],
     parents=["sql"])
_add("mongodb", "MongoDB", "database", ["mongo", "mongoose"], ["nosql"], parents=["nosql"])
_add("redis", "Redis", "database", [], ["caching"])
_add("dynamodb", "DynamoDB", "database", ["dynamo db"], ["nosql", "aws"], parents=["nosql", "aws"])
_add("cassandra", "Cassandra", "database", ["apache cassandra"], ["nosql"], parents=["nosql"])
_add("elasticsearch", "Elasticsearch", "database", ["elastic search", "opensearch", "elk"])
_add("firebase", "Firebase", "database", ["firestore"], ["nosql"])
_add("supabase", "Supabase", "database", [], ["postgresql"])
_add("nosql", "NoSQL", "database", ["non-relational database", "document database"], ["mongodb"])
_add("vector_db", "Vector databases", "database", ["vector database", "vector store", "faiss", "pinecone",
     "chromadb", "chroma", "pgvector", "weaviate", "qdrant", "milvus"], ["rag"])
_add("snowflake", "Snowflake", "database", [], ["data_warehouse"])
_add("bigquery", "BigQuery", "database", ["big query"], ["gcp", "data_warehouse"])
_add("data_warehouse", "Data warehousing", "data", ["data warehouse", "star schema", "dimensional modeling",
     "data modeling", "data modelling"], ["etl"])

# --- Cloud / DevOps ------------------------------------------------------------------------------
_add("aws", "AWS", "cloud", ["amazon web services", "aws cloud"], ["gcp", "azure", "cloud"])
_add("aws_lambda", "AWS Lambda", "cloud", ["lambda functions", "aws lambda"], ["serverless", "aws"], parents=["aws",
     "serverless"])
_add("aws_s3", "Amazon S3", "cloud", ["s3", "amazon s3", "aws s3"], ["aws"], parents=["aws"])
_add("aws_ec2", "Amazon EC2", "cloud", ["ec2", "amazon ec2"], ["aws"], parents=["aws"])
_add("azure", "Azure", "cloud", ["microsoft azure"], ["aws", "gcp", "cloud"])
_add("gcp", "Google Cloud", "cloud", ["gcp", "google cloud platform", "google cloud"], ["aws", "azure", "cloud"])
_add("oci", "Oracle Cloud", "cloud", ["oracle cloud infrastructure", "oci", "oracle cloud"], ["cloud"])
_add("ibm_cloud", "IBM Cloud", "cloud", ["ibm cloud"], ["cloud"])
_add("cloud", "Cloud platforms", "cloud", ["cloud computing", "cloud services", "cloud platforms", "cloud-native",
     "cloud native"], ["aws", "gcp", "azure"])
_add("serverless", "Serverless", "cloud", ["serverless architecture", "faas"], ["aws_lambda"])
_add("vercel", "Vercel", "cloud", [], ["cloud"])
_add("render_host", "Render", "cloud", ["render.com"], ["cloud"])
_add("heroku", "Heroku", "cloud", [], ["cloud"])
_add("docker", "Docker", "devops", ["containers", "containerization", "dockerfile", "docker compose",
     "docker-compose"], ["kubernetes"])
_add("kubernetes", "Kubernetes", "devops", ["k8s", "eks", "gke", "aks", "helm", "openshift"], ["docker"])
_add("terraform", "Terraform", "devops", ["infrastructure as code", "iac"], ["cloudformation"])
_add("cloudformation", "CloudFormation", "devops", ["aws cloudformation", "cdk"], ["terraform", "aws"])
_add("ci_cd", "CI/CD", "devops", ["ci/cd", "cicd", "continuous integration", "continuous delivery",
     "continuous deployment", "build pipelines", "deployment pipelines"], ["github_actions", "jenkins"])
_add("github_actions", "GitHub Actions", "devops", ["gh actions"], ["ci_cd"], parents=["ci_cd"])
_add("jenkins", "Jenkins", "devops", [], ["ci_cd"], parents=["ci_cd"])
_add("gitlab_ci", "GitLab CI", "devops", ["gitlab ci/cd"], ["ci_cd"], parents=["ci_cd"])
_add("linux", "Linux", "devops", ["unix", "ubuntu", "linux administration"], ["bash"])
_add("nginx", "Nginx", "devops", [], ["linux"])
_add("git", "Git", "tool", ["version control", "source control"], ["github"])
_add("github", "GitHub", "tool", [], ["git"])
_add("kafka", "Apache Kafka", "backend", ["kafka"], ["message_queues"])
_add("rabbitmq", "RabbitMQ", "backend", [], ["message_queues"])
_add("message_queues", "Message queues", "backend", ["message queue", "message broker", "pub/sub", "event-driven",
     "event driven", "job queue", "task queue"], ["kafka", "rabbitmq"])
_add("celery", "Celery", "backend", [], ["message_queues"], parents=["message_queues"])
_add("monitoring", "Monitoring & observability", "devops", ["observability", "prometheus", "grafana", "logging",
     "datadog", "new relic", "splunk"])

# --- Data / ML / AI ------------------------------------------------------------------------------
_add("machine_learning", "Machine Learning", "ml", ["ml", "machine-learning", "predictive modeling",
     "predictive modelling"], ["deep_learning", "data_science"])
_add("deep_learning", "Deep Learning", "ml", ["neural networks", "neural network", "dl", "cnn", "rnn", "lstm",
     "convolutional neural networks"], ["machine_learning"], parents=["machine_learning"])
_add("nlp", "NLP", "ml", ["natural language processing", "text classification", "named entity recognition"],
     ["llm", "machine_learning"], parents=["machine_learning"])
_add("computer_vision", "Computer Vision", "ml", ["image processing", "object detection", "image segmentation",
     "semantic segmentation"], ["deep_learning"], parents=["machine_learning"])
_add("llm", "LLMs", "ml", ["large language models", "large language model", "llms", "generative ai", "genai",
     "gen ai", "gpt", "prompt engineering", "llm", "gemini", "openai api", "function calling"],
     ["nlp", "rag"], parents=["machine_learning"])
_add("rag", "RAG", "ml", ["retrieval augmented generation", "retrieval-augmented generation", "rag"],
     ["llm", "vector_db"], parents=["llm"])
_add("langchain", "LangChain", "ml", ["langgraph", "llamaindex", "llama index"], ["llm", "rag"], parents=["llm"])
_add("ai_agents", "AI agents", "ml", ["agentic", "ai agent", "ai agents", "agentic ai", "autonomous agents"],
     ["llm"], parents=["llm"])
_add("tensorflow", "TensorFlow", "ml", ["tf", "tensorflow.js", "tfjs"], ["pytorch", "keras"],
     parents=["deep_learning"])
_add("keras", "Keras", "ml", [], ["tensorflow"], parents=["deep_learning"])
_add("pytorch", "PyTorch", "ml", ["torch"], ["tensorflow"], parents=["deep_learning"])
_add("scikit_learn", "scikit-learn", "ml", ["sklearn", "scikit learn", "scikit-learn"], ["machine_learning"],
     parents=["machine_learning"])
_add("huggingface", "Hugging Face", "ml", ["hugging face", "huggingface", "transformers"], ["pytorch", "nlp"],
     parents=["deep_learning"])
_add("xgboost", "XGBoost", "ml", ["lightgbm", "gradient boosting", "catboost"], ["scikit_learn"],
     parents=["machine_learning"])
_add("opencv", "OpenCV", "ml", ["open cv"], ["computer_vision"], parents=["computer_vision"])
_add("mediapipe", "MediaPipe", "ml", [], ["computer_vision"], parents=["computer_vision"])
_add("reinforcement_learning", "Reinforcement Learning", "ml", ["markov decision process", "mdp",
     "value iteration", "q-learning", "policy iteration"], ["machine_learning"], parents=["machine_learning"])
_add("mlops", "MLOps", "ml", ["model deployment", "ml pipelines", "mlflow", "kubeflow", "model serving"],
     ["machine_learning", "docker"])
_add("pandas", "Pandas", "data", [], ["numpy", "data_analysis"], parents=["data_analysis"])
_add("numpy", "NumPy", "data", [], ["pandas"])
_add("data_analysis", "Data analysis", "data", ["data analytics", "exploratory data analysis", "eda",
     "analytics"], ["pandas", "data_science"])
_add("data_science", "Data science", "data", ["data scientist"], ["machine_learning", "data_analysis"])
_add("data_visualization", "Data visualization", "data", ["data visualisation", "matplotlib", "seaborn", "plotly",
     "dashboards", "dashboard", "recharts", "chart.js"], ["powerbi", "tableau"])
_add("powerbi", "Power BI", "data", ["power bi", "powerbi"], ["tableau", "data_visualization"],
     parents=["data_visualization"])
_add("tableau", "Tableau", "data", [], ["powerbi", "data_visualization"], parents=["data_visualization"])
_add("excel", "Excel", "data", ["ms excel", "microsoft excel", "advanced excel", "spreadsheets"])
_add("etl", "ETL", "data", ["etl pipelines", "data pipelines", "data pipeline", "elt", "data ingestion"],
     ["airflow", "spark"])
_add("spark", "Apache Spark", "data", ["pyspark", "spark"], ["hadoop", "etl"])
_add("hadoop", "Hadoop", "data", ["hdfs", "mapreduce", "hive"], ["spark"])
_add("airflow", "Airflow", "data", ["apache airflow"], ["etl"])
_add("jupyter", "Jupyter", "tool", ["jupyter notebook", "notebooks", "ipython"], ["python"])
_add("streamlit", "Streamlit", "frontend", [], ["python"])
_add("statistics", "Statistics", "data", ["statistical analysis", "probability", "hypothesis testing",
     "regression analysis"], ["data_analysis"])
_add("web_scraping", "Web scraping", "data", ["beautifulsoup", "scrapy", "web crawling"], ["python"])

# --- Testing / quality ---------------------------------------------------------------------------
_add("testing", "Software testing", "testing", ["automated testing", "test automation", "unit testing",
     "unit tests", "integration testing", "integration tests", "qa", "quality assurance", "test cases",
     "software testing", "tdd", "test-driven development"], ["pytest", "junit", "jest"])
_add("pytest", "pytest", "testing", ["py.test"], ["testing"], parents=["testing"])
_add("junit", "JUnit", "testing", ["mockito", "junit5"], ["testing"], parents=["testing"])
_add("jest", "Jest", "testing", ["vitest", "react testing library", "testing library"], ["testing"],
     parents=["testing"])
_add("selenium", "Selenium", "testing", ["webdriver"], ["playwright", "testing"], parents=["testing"])
_add("playwright", "Playwright", "testing", [], ["selenium", "cypress"], parents=["testing"])
_add("cypress", "Cypress", "testing", [], ["playwright"], parents=["testing"])
_add("postman", "Postman", "tool", ["api testing"], ["rest_api"])
_add("debugging", "Debugging", "concept", ["troubleshooting", "root cause analysis"])

# --- Engineering concepts ------------------------------------------------------------------------
_add("rest_api", "REST APIs", "concept", ["rest", "restful", "rest api", "rest apis", "restful apis",
     "restful services", "web services", "api development", "apis", "api design", "http apis"], ["graphql"])
_add("microservices", "Microservices", "concept", ["microservice", "micro-services", "service-oriented architecture",
     "soa", "distributed services"], ["rest_api", "docker"])
_add("system_design", "System design", "concept", ["distributed systems", "scalable systems", "high availability",
     "scalability", "software architecture"], ["microservices"])
_add("oop", "Object-oriented programming", "concept", ["oop", "oops", "object oriented", "object-oriented",
     "object oriented programming", "object-oriented design", "ood"])
_add("dsa", "Data structures & algorithms", "concept", ["data structures", "algorithms", "dsa",
     "data structures and algorithms", "problem solving", "competitive programming"])
_add("dbms", "DBMS", "concept", ["database management systems", "database design", "rdbms", "relational databases",
     "relational database", "normalization", "database schema"], ["sql"])
_add("operating_systems", "Operating systems", "concept", ["operating system", "os concepts", "multithreading",
     "concurrency", "processes and threads"])
_add("computer_networks", "Computer networks", "concept", ["networking", "tcp/ip", "http", "dns", "computer networking"])
_add("sdlc", "SDLC", "concept", ["software development life cycle", "software development lifecycle"], ["agile"])
_add("agile", "Agile", "concept", ["scrum", "kanban", "agile methodologies", "sprint planning", "jira"], ["sdlc"])
_add("authentication", "Authentication", "security", ["auth", "authentication", "login", "oauth", "oauth2",
     "openid connect", "sso", "single sign-on", "session management", "password hashing"], ["jwt", "rbac"])
_add("jwt", "JWT", "security", ["json web token", "json web tokens"], ["authentication"],
     parents=["authentication"])
_add("rbac", "Role-based access control", "security", ["rbac", "role-based access control",
     "role based access control", "authorization", "access control", "permissions"], ["authentication"])
_add("security", "Application security", "security", ["application security", "owasp", "secure coding",
     "web security", "idor", "csrf", "xss", "sql injection", "cybersecurity", "cyber security"], ["authentication"])
_add("caching", "Caching", "concept", ["cache", "memcached", "cdn"], ["redis"])
_add("performance", "Performance optimization", "concept", ["performance tuning", "query optimization",
     "profiling", "latency"])
_add("design_patterns", "Design patterns", "concept", ["solid principles", "solid", "clean code", "clean architecture"],
     ["oop"])
_add("orm", "ORMs", "concept", ["orm", "sqlalchemy", "prisma", "sequelize", "typeorm", "drizzle", "hibernate orm"],
     ["sql"])
_add("db_migrations", "Database migrations", "concept", ["alembic", "flyway", "liquibase", "schema migrations"],
     ["orm"])
_add("api_integration", "API integration", "concept", ["third-party apis", "api integrations", "integrations",
     "webhooks"], ["rest_api"])
_add("browser_extension", "Browser extensions", "frontend", ["chrome extension", "browser extension",
     "web extension", "manifest v3"], ["javascript"])
_add("desktop_apps", "Desktop applications", "frontend", ["desktop application", "electron", "java swing",
     "swing", "tkinter", "pyqt", "javafx"])
_add("cli_tools", "Command-line tools", "concept", ["cli", "command-line", "command line tool"])
_add("responsive_design", "Responsive design", "frontend", ["responsive web design", "mobile-first", "accessibility",
     "a11y", "wcag"], ["css"])
_add("state_management", "Workflow & state management", "concept", ["state machine", "workflow engine",
     "state management", "workflow automation"])
_add("n8n", "n8n", "tool", [], ["api_integration"])
_add("sap", "SAP", "tool", ["sap erp", "sap s/4hana", "s/4hana", "sap hana", "hana"], ["sap_btp"])
_add("salesforce", "Salesforce", "tool", ["apex", "salesforce crm"])
_add("pdf_processing", "PDF processing", "concept", ["pdf parsing", "pdf.js", "pdfjs", "document parsing"])
_add("speech_to_text", "Speech-to-text", "ml", ["speech recognition", "asr", "whisper", "speech to text"],
     ["nlp"], parents=["machine_learning"])

SKILLS: dict[str, SkillDef] = {s.key: s for s in _S}
CATEGORY_LABELS = {
    "language": "Languages",
    "frontend": "Frontend",
    "backend": "Backend",
    "database": "Databases",
    "cloud": "Cloud",
    "devops": "DevOps",
    "ml": "AI / ML",
    "data": "Data",
    "testing": "Testing",
    "tool": "Tools",
    "concept": "Engineering",
    "security": "Security",
    "mobile": "Mobile",
}

# Package names (lower-case) in dependency manifests -> skill keys.
PACKAGE_SKILLS: dict[str, tuple[str, ...]] = {
    # Python
    "fastapi": ("fastapi", "rest_api"), "django": ("django",), "djangorestframework": ("django", "rest_api"),
    "flask": ("flask",), "sqlalchemy": ("orm",), "alembic": ("db_migrations",), "pydantic": ("python",),
    "psycopg": ("postgresql",), "psycopg2": ("postgresql",), "psycopg2-binary": ("postgresql",),
    "asyncpg": ("postgresql",), "pymysql": ("mysql",), "mysql-connector-python": ("mysql",),
    "pymongo": ("mongodb",), "motor": ("mongodb",), "redis": ("redis",), "celery": ("celery",),
    "pandas": ("pandas",), "numpy": ("numpy",), "scikit-learn": ("scikit_learn",), "sklearn": ("scikit_learn",),
    "tensorflow": ("tensorflow",), "tensorflow-cpu": ("tensorflow",), "keras": ("keras",), "torch": ("pytorch",),
    "torchvision": ("pytorch", "computer_vision"), "transformers": ("huggingface",), "datasets": ("huggingface",),
    "langchain": ("langchain",), "langgraph": ("langchain", "ai_agents"), "llama-index": ("langchain",),
    "openai": ("llm",), "google-generativeai": ("llm",), "google-genai": ("llm",), "sentence-transformers": ("nlp",),
    "faiss-cpu": ("vector_db",), "chromadb": ("vector_db",), "pinecone-client": ("vector_db",),
    "pgvector": ("vector_db",), "spacy": ("nlp",), "nltk": ("nlp",), "opencv-python": ("opencv",),
    "opencv-python-headless": ("opencv",), "mediapipe": ("mediapipe",), "xgboost": ("xgboost",),
    "lightgbm": ("xgboost",), "matplotlib": ("data_visualization",), "seaborn": ("data_visualization",),
    "plotly": ("data_visualization",), "streamlit": ("streamlit",), "pytest": ("pytest",),
    "selenium": ("selenium",), "playwright": ("playwright",), "boto3": ("aws",), "beautifulsoup4": ("web_scraping",),
    "scrapy": ("web_scraping",), "pyspark": ("spark",), "apache-airflow": ("airflow",), "jupyter": ("jupyter",),
    "pyjwt": ("jwt",), "python-jose": ("jwt",), "passlib": ("authentication",), "argon2-cffi": ("authentication",),
    "bcrypt": ("authentication",), "websockets": ("websockets",), "grpcio": ("grpc",), "kafka-python": ("kafka",),
    "pika": ("rabbitmq",), "openai-whisper": ("speech_to_text",), "pylint": ("python",), "gitpython": ("git",),
    "pypdf": ("pdf_processing",), "pdfplumber": ("pdf_processing",), "pymupdf": ("pdf_processing",),
    "uvicorn": ("fastapi",), "gunicorn": ("python",), "httpx": ("api_integration",), "requests": ("api_integration",),
    "tkinter": ("desktop_apps",), "pyqt5": ("desktop_apps",), "pyside6": ("desktop_apps",),
    # JavaScript / TypeScript
    "react": ("react",), "react-dom": ("react",), "next": ("nextjs",), "vue": ("vue",), "@angular/core": ("angular",),
    "svelte": ("svelte",), "@sveltejs/kit": ("svelte",), "express": ("express",), "@nestjs/core": ("nestjs",),
    "typescript": ("typescript",), "tailwindcss": ("tailwind",), "bootstrap": ("bootstrap",), "vite": ("vite",),
    "webpack": ("webpack",), "redux": ("redux",), "@reduxjs/toolkit": ("redux",), "mongoose": ("mongodb",),
    "mongodb": ("mongodb",), "pg": ("postgresql",), "postgres": ("postgresql",), "mysql": ("mysql",),
    "mysql2": ("mysql",), "sqlite3": ("sqlite",), "better-sqlite3": ("sqlite",), "prisma": ("orm",),
    "@prisma/client": ("orm",), "drizzle-orm": ("orm",), "sequelize": ("orm",), "typeorm": ("orm",),
    "drizzle-kit": ("db_migrations",), "knex": ("orm",), "socket.io": ("websockets",), "ws": ("websockets",),
    "jest": ("jest",), "vitest": ("jest",), "@testing-library/react": ("jest",), "cypress": ("cypress",),
    "@playwright/test": ("playwright",), "playwright": ("playwright",), "three": ("threejs",),
    "@react-three/fiber": ("threejs",), "d3": ("d3",), "chart.js": ("data_visualization",),
    "recharts": ("data_visualization",), "firebase": ("firebase",), "@supabase/supabase-js": ("supabase",),
    "jsonwebtoken": ("jwt",), "passport": ("authentication",), "bcrypt": ("authentication",),
    "bcryptjs": ("authentication",), "graphql": ("graphql",), "@apollo/server": ("graphql",),
    "@apollo/client": ("graphql",), "electron": ("desktop_apps",), "react-native": ("react_native",),
    "@tensorflow/tfjs": ("tensorflow",), "@mediapipe/tasks-vision": ("mediapipe",), "@mediapipe/hands": ("mediapipe",),
    "openai": ("llm",), "@google/generative-ai": ("llm",), "@google/genai": ("llm",), "langchain": ("langchain",),
    "@langchain/core": ("langchain",), "pdfjs-dist": ("pdf_processing",), "pdf-parse": ("pdf_processing",),
    "mammoth": ("pdf_processing",), "@sap/cds": ("sap_cap",), "@sap/cds-dk": ("sap_cap",), "@ui5/cli": ("sapui5",),
    "redis": ("redis",), "ioredis": ("redis",), "bullmq": ("message_queues",), "kafkajs": ("kafka",),
    "amqplib": ("rabbitmq",), "aws-sdk": ("aws",), "@aws-sdk/client-s3": ("aws_s3",), "zod": ("typescript",),
    "@tanstack/react-query": ("react",), "react-router-dom": ("react",), "googleapis": ("api_integration",),
    "stripe": ("api_integration",), "nodemailer": ("api_integration",), "puppeteer": ("web_scraping",),
    # JVM
    "spring-boot-starter-web": ("spring_boot", "rest_api"), "spring-boot-starter": ("spring_boot",),
    "spring-boot-starter-data-jpa": ("hibernate",), "spring-boot-starter-security": ("authentication",),
    "spring-boot-starter-test": ("junit",), "junit": ("junit",), "junit-jupiter": ("junit",), "mockito-core": ("junit",),
    "mysql-connector-java": ("mysql",), "mysql-connector-j": ("mysql",), "postgresql": ("postgresql",),
    "sqlite-jdbc": ("sqlite",), "hibernate-core": ("hibernate",), "jjwt": ("jwt",), "jjwt-api": ("jwt",),
    "kafka-clients": ("kafka",), "lombok": ("java",),
}

# Files or paths in a repository tree -> (skill keys, human-readable reason).
FILE_MARKERS: list[tuple[re.Pattern, tuple[str, ...], str]] = [
    (re.compile(r"(^|/)Dockerfile(\.[\w-]+)?$"), ("docker",), "Dockerfile"),
    (re.compile(r"(^|/)(docker-)?compose\.ya?ml$"), ("docker",), "Docker Compose file"),
    (re.compile(r"^\.github/workflows/[^/]+\.ya?ml$"), ("github_actions", "ci_cd"), "GitHub Actions workflow"),
    (re.compile(r"^\.gitlab-ci\.yml$"), ("gitlab_ci", "ci_cd"), "GitLab CI configuration"),
    (re.compile(r"(^|/)Jenkinsfile$"), ("jenkins", "ci_cd"), "Jenkinsfile"),
    (re.compile(r"(^|/)vercel\.json$"), ("vercel",), "Vercel configuration"),
    (re.compile(r"(^|/)render\.ya?ml$"), ("render_host",), "Render Blueprint"),
    (re.compile(r"(^|/)Procfile$"), ("heroku",), "Procfile"),
    (re.compile(r"\.tf$"), ("terraform",), "Terraform configuration"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), ("serverless",), "Serverless Framework configuration"),
    (re.compile(r"(^|/)(template|samconfig)\.ya?ml$"), ("aws",), "AWS SAM configuration"),
    (re.compile(r"(^|/)(k8s|kubernetes|helm|charts)/"), ("kubernetes",), "Kubernetes manifests"),
    (re.compile(r"(^|/)alembic(\.ini|/)"), ("db_migrations",), "Alembic migrations"),
    (re.compile(r"(^|/)migrations/"), ("db_migrations",), "database migrations directory"),
    (re.compile(r"\.ipynb$"), ("jupyter",), "Jupyter notebook"),
    (re.compile(r"(^|/)(pytest\.ini|conftest\.py)$"), ("pytest",), "pytest configuration"),
    (re.compile(r"(^|/)(tests?|__tests__|spec)/"), ("testing",), "test directory"),
    (re.compile(r"\.(test|spec)\.(t|j)sx?$"), ("testing",), "test files"),
    (re.compile(r"(^|/)test_[\w]+\.py$"), ("testing",), "test files"),
    (re.compile(r"(^|/)(playwright\.config\.(t|j)s)$"), ("playwright",), "Playwright configuration"),
    (re.compile(r"(^|/)cypress\.config\.(t|j)s$"), ("cypress",), "Cypress configuration"),
    (re.compile(r"(^|/)pom\.xml$"), ("java",), "Maven build"),
    (re.compile(r"(^|/)build\.gradle(\.kts)?$"), ("java",), "Gradle build"),
    (re.compile(r"(^|/)CMakeLists\.txt$"), ("cpp",), "CMake build"),
    (re.compile(r"(^|/)tailwind\.config\.(c?js|ts|mjs)$"), ("tailwind",), "Tailwind configuration"),
    (re.compile(r"(^|/)vite\.config\.(t|j)s$"), ("vite",), "Vite configuration"),
    (re.compile(r"(^|/)next\.config\.(m?js|ts)$"), ("nextjs",), "Next.js configuration"),
    (re.compile(r"(^|/)mta\.ya?ml$"), ("sap_btp",), "SAP MTA descriptor"),
    (re.compile(r"(^|/)\.cdsrc\.json$"), ("sap_cap",), "SAP CAP configuration"),
    (re.compile(r"(^|/)nginx\.conf$"), ("nginx",), "Nginx configuration"),
    (re.compile(r"(^|/)schema\.prisma$"), ("orm",), "Prisma schema"),
    (re.compile(r"(^|/)openapi\.(ya?ml|json)$"), ("rest_api",), "OpenAPI specification"),
]

# Files whose names suggest a concept. These produce *inferred* evidence only.
CONCEPT_FILE_HINTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"(^|/)(rbac|roles?|permissions?|authorization|policies)(\.\w+|/)", re.I), "rbac",
     "role/permission modules in the source tree"),
    (re.compile(r"(^|/)(auth|authentication|login|security)(\.\w+|/)", re.I), "authentication",
     "authentication modules in the source tree"),
    (re.compile(r"(^|/)(workers?|queue|jobs|tasks)(\.\w+|/)", re.I), "message_queues",
     "background worker / queue modules in the source tree"),
    (re.compile(r"(^|/)(routers?|routes|controllers?|endpoints|api)(/|\.\w+$)", re.I), "rest_api",
     "API route/controller modules in the source tree"),
    (re.compile(r"(^|/)manifest\.json$", re.I), "browser_extension", "a manifest.json (possibly a browser extension)"),
]

# Languages as GitHub reports them -> skill keys.
GITHUB_LANGUAGES = {
    "Python": "python", "Java": "java", "JavaScript": "javascript", "TypeScript": "typescript", "C++": "cpp",
    "C": "c", "C#": "csharp", "Go": "go", "Rust": "rust", "Kotlin": "kotlin", "Swift": "swift", "Ruby": "ruby",
    "PHP": "php", "Scala": "scala", "R": "r", "MATLAB": "matlab", "Shell": "bash", "HTML": "html", "CSS": "css",
    "SCSS": "css", "Dart": "dart", "Jupyter Notebook": "jupyter", "Dockerfile": "docker", "PLpgSQL": "postgresql",
    "TSQL": "sql", "ABAP": "abap", "Solidity": "solidity", "Vue": "vue", "Svelte": "svelte", "HCL": "terraform",
    "CAP CDS": "sap_cap",
}


@dataclass
class TermMatch:
    key: str
    alias: str
    start: int
    end: int


@dataclass
class _Matcher:
    patterns: list[tuple[re.Pattern, str, str]] = field(default_factory=list)  # (regex, key, alias)


_BOUND_L = r"(?<![A-Za-z0-9_+#.-])"
_BOUND_R = r"(?![A-Za-z0-9_+#-]|\.[A-Za-z0-9])"


def _alias_regex(alias: str, strict: bool) -> re.Pattern:
    body = re.escape(alias).replace(r"\ ", r"[\s-]+")
    if strict:
        # Strict aliases (single letters, common words) only count inside skill lists: "Python, R, SQL".
        return re.compile(rf"(?:(?<=[,/(|:])|(?<=,\s)|(?<=\band\s)|(?<=\bor\s)|(?<=^))\s*{body}(?=\s*(?:[,/)|;]|$|\band\b|\bor\b))",
                          re.M)
    return re.compile(_BOUND_L + body + _BOUND_R, re.I)


@lru_cache(maxsize=1)
def _matcher() -> _Matcher:
    m = _Matcher()
    entries: list[tuple[str, str, bool]] = []
    for s in SKILLS.values():
        strict_low = {a.lower() for a in s.strict_aliases}
        names = {s.name.lower(), *[a.lower() for a in s.aliases]}
        for n in names:
            if len(n) <= 1 or n in strict_low:
                continue
            entries.append((n, s.key, False))
        for a in s.strict_aliases:
            entries.append((a, s.key, True))
    # Longest aliases first so "spring boot" wins over "spring" and "react native" over "react".
    entries.sort(key=lambda e: -len(e[0]))
    for alias, key, strict in entries:
        m.patterns.append((_alias_regex(alias, strict), key, alias))
    return m


# Aliases that are too generic to count on their own outside explicit skill lists.
_WEAK_ALIASES = {"node", "spring", "rest", "cap", "apis", "auth", "login", "cache", "cli", "analytics", "dashboard",
                 "dashboards", "notebooks", "permissions", "integrations", "logging", "http", "networking", "ml", "dl", "concurrency", "multithreading",
                 "bootstrap", "render", "express", "spring", "vite", "agile", "scrum", "kanban", "testing", "security",
                 "ts", "tf", "solid", "jira", "fiori", "transformers", "mongo", "hive", "hana", "sprint planning",
                 "authorization", "access control", "problem solving", "algorithms", "containers", "llm", "gpt",
                 "rag", "api testing", "asr", "orm", "iac", "soa", "eda", "qa", "sso", "dns"}


def find_skills(text: str, *, include_weak: bool = True) -> list[TermMatch]:
    """Finds taxonomy skills mentioned in free text. Overlapping matches keep the longest alias."""
    if not text:
        return []
    taken: list[tuple[int, int]] = []
    out: list[TermMatch] = []
    for rx, key, alias in _matcher().patterns:
        if not include_weak and alias in _WEAK_ALIASES:
            continue
        for mt in rx.finditer(text):
            s, e = mt.span()
            # trim leading whitespace captured by strict patterns
            while s < e and text[s].isspace():
                s += 1
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            out.append(TermMatch(key, alias, s, e))
    out.sort(key=lambda t: t.start)
    return out


def skill_keys_in(text: str, *, include_weak: bool = True) -> set[str]:
    return {m.key for m in find_skills(text, include_weak=include_weak)}


def canonicalize(name: str) -> str:
    """Maps a free-form skill name to a taxonomy key, or a normalised slug when unknown."""
    raw = name.strip()
    if not raw:
        return ""
    low = raw.lower()
    for s in SKILLS.values():
        if low == s.key or low == s.name.lower() or low in (a.lower() for a in s.aliases) or raw in s.strict_aliases:
            return s.key
    found = find_skills(raw)
    if len(found) == 1 and (found[0].end - found[0].start) >= len(raw) - 2:
        return found[0].key
    return "x:" + re.sub(r"[^a-z0-9+#.]+", "-", low).strip("-")


def display_name(key: str) -> str:
    if key in SKILLS:
        return SKILLS[key].name
    return key.removeprefix("x:").replace("-", " ").strip().title() if key.startswith("x:") else key


def category_of(key: str) -> str:
    return SKILLS[key].category if key in SKILLS else "other"


def related_keys(key: str) -> set[str]:
    """Skills that count as partial evidence for `key` (symmetric relations plus parent/child links)."""
    if key not in SKILLS:
        return set()
    out = set(SKILLS[key].related)
    for s in SKILLS.values():
        if key in s.related:
            out.add(s.key)
        if key in s.parents:  # a child skill (FastAPI) is partial evidence of nothing more; handled as implies
            pass
    out.discard(key)
    return out


def implied_keys(key: str) -> set[str]:
    """Broader skills that `key` directly demonstrates (FastAPI -> Python, REST APIs)."""
    seen: set[str] = set()
    stack = [key]
    while stack:
        k = stack.pop()
        if k not in SKILLS:
            continue
        for p in SKILLS[k].parents:
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen
