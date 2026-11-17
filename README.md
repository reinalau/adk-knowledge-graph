# ADK - Knowledge Graph multiagente con Workflow (Graph)

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)
![Google ADK](https://img.shields.io/badge/Google_ADK-2.12-4285F4?style=flat&logo=google&logoColor=white)
![Amazon Nova](https://img.shields.io/badge/Amazon_Nova-Bedrock-FF9900?style=flat&logo=amazonaws&logoColor=white)

Versión en Google ADK del pipeline de [strands-graph](https://github.com/reinalau/strands-graph): mismo caso de uso (extracción de un knowledge graph desde texto), mismos schemas y misma topología **paralelismo → agregación → feedback loop**. Usa los workflows basados en grafos de ADK 2.x (`Workflow`) y corre con **Amazon Nova en Bedrock** vía LiteLLM (Gemini es opcional).

## Para qué sirve

El agente convierte texto libre (por ejemplo, el abstract o un fragmento de un paper) en un **knowledge graph**: una lista de entidades y de relaciones con la forma *sujeto → predicado → objeto*.

```json
{ "subject": "yoshua bengio", "predicate": "founded", "object": "mila" }
```

Ese formato estructurado es legible por personas y por otros sistemas, y sirve para:

- **Revisión de literatura:** resumir quién hizo qué, dónde y con qué resultado, sin leer el texto completo.
- **Conectar conocimiento entre documentos:** al unir los grafos de varios papers aparecen autores, instituciones o conceptos compartidos.
- **Cargar una base de grafos** (por ejemplo, Amazon Neptune o Neo4j) para consultar relaciones.
- **GraphRAG:** dar a un LLM relaciones explícitas como contexto, en lugar de fragmentos de texto sueltos.

Es un ejemplo educativo: los textos son cortos y la calidad depende del modelo. El objetivo es mostrar la orquestación (paralelismo, agregación, validación y loop de revisión), no ofrecer un extractor listo para producción.

## Topología

```
START -> split_text -> extractor_1 -+
                    -> extractor_2 -+-> join_extractions -> aggregator -> validator -> route_verdict
                    -> extractor_3 -+                         ^                          |
                                                              +-------- "revise" --------+
                                                                        "publish" -> publish_graph

split_text, route_verdict, publish_graph : Python function nodes (no LLM)
extractor_n, aggregator, validator       : LLM agents with output_schema
join_extractions                         : JoinNode (waits for all extractors)
```

### Qué hace cada nodo

| Nodo | Tipo | Qué hace |
| :--- | :--- | :--- |
| `split_text` | Función | Divide el texto en 3 chunks consecutivos con una cantidad similar de oraciones, sin cortar ninguna. Los etiqueta (`--- CHUNK n ---`), los guarda en el state (`chunks`) y reinicia `revisions` y `validation` para que cada mensaje empiece limpio. |
| `extractor_1..3` | Agente LLM | Se ejecutan en paralelo. Cada uno extrae entidades y relaciones solo de su chunk y devuelve un `KnowledgeGraph` (`output_schema`) que guarda en `triples_n`. |
| `join_extractions` | `JoinNode` | Barrera de sincronización: espera a que terminen los 3 extractores antes de continuar. |
| `aggregator` | Agente LLM | Fusiona los 3 grafos parciales: elimina duplicados, normaliza nombres y separa entidades compuestas. En una revisión, lee el feedback anterior desde `{validation?}`. Guarda el resultado en `kg_draft`. |
| `validator` | Agente LLM | Evalúa el borrador contra el texto original con 4 criterios (formato, consistencia, completitud y sesgo) y devuelve un `ValidatorOutput` con veredicto e issues. |
| `route_verdict` | Función | Decide en código el camino. Publica si el validator aprueba y el chequeo determinista (`find_missing_entities`) no encuentra entidades faltantes. Si no, agrega los issues detectados y vuelve al `aggregator` (ruta `revise`). Corta el loop tras `MAX_REVISIONS = 2` revisiones. |
| `publish_graph` | Función | Valida el grafo con Pydantic, agrega entidades faltantes si se agotó el presupuesto de revisiones (lo registra en `entities_repaired`) y publica el resultado en `kg_final`. Las entidades que no participan en ninguna relación se mueven a `unlinked_entities`: el grafo queda limpio y no se pierde nada de lo extraído. |

## Equivalencias Strands → ADK

| Strands (Graph) | ADK (`Workflow`) |
| :--- | :--- |
| `GraphBuilder` con nodos y aristas | `Workflow(edges=[...])` |
| `all_dependencies_complete()` para forzar AND (el default en Python es OR) | `JoinNode`: espera a todos los predecesores |
| Condiciones `needs_revision` / `is_approved` en las aristas | Nodo `route_verdict` que emite `Event(route=...)` y suma un chequeo determinista |
| `set_max_node_executions(12)` | Contador de revisiones en el state (`MAX_REVISIONS = 2`) |
| Coordinator y Publisher como agentes LLM | Nodos de función: deterministas y sin tokens |
| `structured_output_model` | `output_schema` + `output_key` |
| `strands-agents-evals` | `adk eval` + `evals/run_evals.py` |

## Project Structure

```
adk-knowledge-graph/
├── kg_pipeline/              # ADK app package (loaded by adk web / adk eval)
│   ├── agent.py              # Exposes root_agent
│   ├── graph.py              # Workflow definition: nodes and edges
│   ├── nodes.py              # Function nodes: split_text, route_verdict, publish_graph
│   ├── llm_agents.py         # LLM nodes: extractors, aggregator, validator
│   ├── schemas.py            # Pydantic schemas: Relation, KnowledgeGraph, ValidatorOutput
│   ├── config.py             # Model selection: Nova (Bedrock) or Gemini
│   └── runner.py             # Programmatic run (Runner + InMemorySessionService)
├── kg_pipeline_docs/         # Long-document version (second ADK app, reuses kg_pipeline)
│   ├── agent.py              # Exposes root_agent
│   ├── graph.py              # Workflow: dynamic fan-out + merge + entity-resolution loop
│   ├── nodes.py              # split_into_chunks, extract_chunks (dynamic), merge, apply aliases
│   ├── llm_agents.py         # Per-chunk extractor, entity resolver, duplicate validator
│   ├── splitter.py           # Section-aware chunking with word limit and chunk cap
│   ├── merge.py              # Deterministic merge, alias candidates and alias application
│   └── schemas.py            # EntityAlias, EntityResolution, DuplicateCheck
├── main.py                   # CLI: runs kg_pipeline and exports the graph to outputs/
├── main_docs.py              # CLI: runs kg_pipeline_docs on one or more .txt files in one session
├── agentcore_app.py          # AgentCore Runtime entry point (BedrockAgentCoreApp) for kg_pipeline_docs
├── deploy/
│   ├── requirements.txt      # Runtime dependencies packaged into the zip
│   ├── build_package.py      # Builds build/agentcore_package.zip (Linux arm64 wheels, no Docker)
│   └── invoke.py             # Invokes the deployed runtime with several texts in one session
├── infra/terraform/          # S3 bucket + package, IAM execution role, AgentCore Runtime
├── examples/
│   ├── sample_input.txt      # Default input text
│   └── papers/
│       ├── butterfly_migration_abstract.txt    # Real paper abstract for kg_pipeline_docs
│       └── butterfly_migration_abstract_l.txt  # Introduction of the same paper (longer text)
├── tests/                    # Unit tests for both apps and the AgentCore adapter, no LLM calls
├── evals/
│   ├── test_cases/           # Input texts + hand-annotated expected triples
│   ├── metrics.py            # Deterministic metrics: P/R/F1, entity recall, compound entities
│   ├── run_evals.py          # Runs the workflow per case and writes outputs/eval_report.json
│   ├── kg_pipeline.evalset.json  # Eval set for adk eval
│   └── test_config.json      # adk eval criteria (Nova as judge)
└── outputs/                  # Generated graphs and reports (gitignored)
```

## Requisitos

- Python 3.12 o superior.
- Cuenta de AWS con acceso a Amazon Nova en Bedrock (`us-east-1`) y credenciales configuradas (`aws configure`, variables de entorno o rol IAM).
- Opcional: API key de Gemini si se usa `MODEL_PROVIDER=gemini`.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate       # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env            # default: Nova 2 Lite on Bedrock (Nova Pro as alternative)
```

## Ejecución

```bash
python -m pytest                # unit tests, no cost
python main.py                  # real run on examples/sample_input.txt
python main.py my_text.txt      # real run on another file
```

La salida muestra el recorrido real del grafo, con el número de ejecución de cada nodo (por ejemplo `aggregator@2` indica la segunda pasada por el loop), y guarda el resultado en `outputs/`:

```json
{
  "knowledge_graph": { "entities": ["..."], "relations": [{ "subject": "...", "predicate": "...", "object": "..." }] },
  "unlinked_entities": ["entities extracted without any relation"]
}
```

## Probar con ADK Web

El comando `adk` lo instala `google-adk` dentro del entorno virtual, así que primero activalo. La región y el modelo salen del `.env` (copiado de `.env.example` en la instalación).

```bash
# From the project root
source .venv/bin/activate        # Windows (Git Bash): source .venv/Scripts/activate
adk web .                        # lists kg_pipeline and kg_pipeline_docs
```

1. Abrir `http://localhost:8000` y elegir `kg_pipeline` (o `kg_pipeline_docs` para textos largos).
2. Pegar en el chat un párrafo de varias oraciones (por ejemplo, el de `examples/sample_input.txt`). Cada mensaje ejecuta el grafo completo.
3. Para un texto nuevo, usar **New session**. `split_text` reinicia el contador de revisiones y el feedback del validator, pero una sesión nueva deja el state limpio.

La vista del grafo resalta el nodo del evento seleccionado en el panel de eventos, no todo el recorrido. Para seguir la ejecución completa, recorrer los eventos en orden.

## Visualizar y exportar trazas

La pestaña de trazas de `adk web` agrupa los spans por mensaje: nodos, llamadas al modelo, tokens y duración. Para exportarlas en JSON, con `adk web` en ejecución:

```bash
# Session id: shown at the top of the ADK Web UI
SESSION_ID="<SESSION_ID>"

# Full trace of the session (spans with tokens, timings, LLM request/response)
curl -s "http://127.0.0.1:8000/dev/apps/kg_pipeline/debug/trace/session/$SESSION_ID" -o trace.json

# Session events and final state
curl -s "http://127.0.0.1:8000/apps/kg_pipeline/users/user/sessions/$SESSION_ID" -o session.json
```

Datos útiles de cada span:

| Atributo | Contenido |
| :--- | :--- |
| `name` | `invoke_node <node>`, `invoke_agent <agent>`, `call_llm` |
| `start_time` / `end_time` | Nanosegundos; permiten ver el paralelismo de los extractores |
| `gen_ai.usage.input_tokens` / `output_tokens` | Tokens por llamada al modelo |
| `gcp.vertex.agent.llm_request` / `llm_response` | Prompt y respuesta completos |

Nota: la primera llamada del proceso a Bedrock tarda varios segundos más porque inicializa el cliente (LiteLLM, boto3, credenciales y conexión TLS). Las siguientes ejecuciones del mismo proceso no lo repiten.

## Evaluación

**Métricas deterministas** (gastan tokens porque ejecutan el grafo real):

```bash
python -m evals.run_evals            # all cases
python -m evals.run_evals pioneers   # one case
```

| Métrica | Qué mide |
| :--- | :--- |
| Precision / Recall / F1 | Coincidencia exacta de triples normalizados contra el ground truth (estricta a propósito) |
| `entity_recall` | Proporción de entidades esperadas presentes en el grafo |
| `compound_entities` | Entidades tipo "A and B" que debieron separarse |
| `revisions` | Vueltas del loop validator → aggregator |

**Evaluación nativa de ADK** con Nova como juez:

```bash
adk eval kg_pipeline evals/kg_pipeline.evalset.json --config_file_path evals/test_config.json --print_detailed_results
```

Usa `final_response_match_v2` (LLM como juez, marcado como experimental en ADK) y reporta además llamadas al modelo, tokens y duración.

## Versión para textos largos (aproximación)

`kg_pipeline` usa 3 extractores fijos para mantener la comparación con la versión de Strands. Para documentos largos (por ejemplo, un paper exportado a `.txt`), el repo incluye una segunda app, `kg_pipeline_docs`. Es una aproximación educativa, no una solución de producción. Para llevarlo a producción haría falta reforzar los controles deterministas, evaluar la calidad con un conjunto de documentos anotados y, si las métricas lo justifican, sumar agentes de depuración del grafo o un modelo más capaz (este mismo repositorio puede evolucionar hacia eso)

```
START -> split_into_chunks -> extract_chunks (N extractors, dynamic) -> merge_partial_graphs
      -> entity_resolver -> apply_entity_aliases -> duplicate_validator -> route_verdict
              ^                                                                |
              +------------------------------ "revise" ------------------------+
                                              "publish" -> publish_graph
```

| Nodo | Tipo | Qué hace |
| :--- | :--- | :--- |
| `split_into_chunks` | Función | Detecta secciones por encabezados, descarta `References` y `Acknowledgments`, y arma chunks de hasta 350 palabras (máximo 20 chunks, unas 7.000 palabras). |
| `extract_chunks` | Nodo dinámico | Lanza un extractor por chunk con `ctx.run_node` y `asyncio.gather`, con un máximo de 5 llamadas en paralelo. Si un chunk falla, sigue con el resto. |
| `merge_partial_graphs` | Función | Une los N grafos parciales en código (sin LLM) y elimina duplicados. Aplica los alias seguros en cualquier dominio: variantes de singular y plural, y siglas definidas en el texto (`artificial intelligence (AI)`). Genera como candidatos solo los nombres propios contenidos en otro (`Hinton` → `Geoffrey Hinton`). |
| `entity_resolver` | Agente LLM | Confirma o descarta cada par candidato. No busca alias por su cuenta. |
| `apply_entity_aliases` | Función | Aplica solo pares confirmados que estén entre los candidatos, reescribe las relaciones y elimina duplicados y auto-relaciones. |
| `duplicate_validator` | Agente LLM | Revisa los candidatos que quedaron sin unir y señala los que deberían unirse. |
| `route_verdict` / `publish_graph` | Función | Los mismos de `kg_pipeline`. |

Diferencias de diseño respecto de `kg_pipeline`:

- **El LLM no reescribe el grafo completo.** La unión se hace en código y el modelo solo devuelve pares de alias. Así la salida no crece con el largo del documento.
- **Reglas genéricas, válidas para cualquier paper.** Las entidades son sustantivos o frases nominales (incluidos los nombres propios); el prompt excluye verbos, pronombres y palabras genéricas. Para unir alias, el código no sabe nada del dominio:

  | Caso | Tratamiento |
  | :--- | :--- |
  | Mayúsculas, singular y plural (`line of evidence` / `lines of evidence`) | Se une en código |
  | Sigla definida en el texto (`artificial intelligence (AI)`) | Se une en código |
  | Nombre propio contenido en otro (`Hinton` / `Geoffrey Hinton`) | Candidato: lo confirma el LLM |
  | Sustantivo común contenido en otro (`species` / `migratory species`) | Nunca se une: el modificador cambia el significado |
  | Entidad compuesta (`Pieridae and Nymphalidae`) | Nunca es candidata |

  Los nombres propios se detectan por las mayúsculas del texto original: una palabra suelta al inicio de una oración no cuenta, y los conectores como `of` o `de` se admiten (`University of Toronto`).
- **El código propone y el LLM confirma.** En las pruebas, un LLM buscando alias libremente propuso uniones incorrectas (`deep learning` → `ai research`, `mila` → `montreal`), y una entidad compuesta mal tratada unió dos familias de mariposas en una sola. Con candidatos generados en código y estas reglas, esas uniones no pueden ocurrir.

```bash
python main_docs.py path/to/paper.txt
# Optional limits (environment variables)
# DOCS_MAX_WORDS_PER_CHUNK=350  DOCS_MAX_CHUNKS=20  DOCS_MAX_PARALLEL_EXTRACTORS=5
```

Ejemplo con un abstract real. Es un solo párrafo de unas 280 palabras, así que se reduce el tamaño de chunk para que se ejecuten varios extractores en paralelo:

```bash
DOCS_MAX_WORDS_PER_CHUNK=100 python main_docs.py examples/papers/butterfly_migration_abstract.txt
```

Fuente del abstract, citado con fines educativos: Chowdhury, S., Fuller, R. A., Dingle, H., Chapman, J. W. & Zalucki, M. P. (2021). *Migration in butterflies: a global overview*. Biological Reviews, 96, 1462–1483. [doi:10.1111/brv.12714](https://doi.org/10.1111/brv.12714).

Limitaciones: solo acepta texto plano, la detección de encabezados es heurística y la completitud depende del modelo. Los documentos que superan el máximo de chunks se procesan parcialmente; la cantidad omitida se informa en la salida.


## Hallazgos (ADK 2.12.0)

1. **Las clases de plantilla están deprecadas en el código, pero no en la documentación.** `SequentialAgent`, `ParallelAgent` y `LoopAgent` emiten `DeprecationWarning` en favor de `Workflow`; la documentación todavía las presenta como opción válida.
2. **Un ciclo necesita una arista con ruta.** Un ciclo formado solo por aristas sin condición se rechaza al construir el `Workflow`.
3. **Nova Lite (v1) devolvió `"relations": [{}, ...]`** al consolidar las tres extracciones en `kg_pipeline`. Nova 2 Lite y Nova Pro no lo hicieron en ese paso. `publish_graph` valida con Pydantic y falla en lugar de publicar un grafo vacío.
4. **`adk eval` acepta un modelo de Bedrock como juez** (`"judge_model": "bedrock/..."`), sin depender de la cuota de Gemini.
5. **Un validador LLM chico aprueba grafos inconsistentes.** En una prueba, Nova 2 Lite marcó `consistent: true` en un grafo con 4 entidades usadas en relaciones pero ausentes de `entities`. Por eso `route_verdict` combina el veredicto del LLM con un chequeo en código (`find_missing_entities`). Si falla, devuelve el borrador al aggregator con el problema puntual. Si se agota el presupuesto de revisiones, `publish_graph` agrega las entidades faltantes.
6. **ADK 2.12 no incluye servicios de sesión sobre S3 ni DynamoDB** (sí in-memory, SQLite, base de datos SQL y Vertex AI). Strands incluye `S3SessionManager`.
7. **La primera llamada de cada proceso a Bedrock tarda unos 7 s más.** El cliente (LiteLLM, boto3, credenciales y conexión TLS) se inicializa en esa llamada y bloquea brevemente el event loop: en la traza, un extractor arranca antes y los demás esperan. Las ejecuciones siguientes del mismo proceso no lo repiten.

### Hallazgos de `kg_pipeline_docs` (agentes para textos largos)

Estos puntos surgieron al probar la segunda app con un abstract real y con la introducción de un paper (unas 1.800 palabras):

1. **El paralelismo dinámico no aparece en el diagrama de `adk web`.** La vista de grafo dibuja solo las aristas declaradas en `Workflow(edges=[...])`. Los extractores creados con `ctx.run_node` se ven como un único nodo (`extract_chunks`); las ejecuciones individuales aparecen en los paneles de eventos y trazas.
2. **Una respuesta vacía del modelo llega como `None`, sin error.** ADK no lanza una excepción, así que el chunk se perdía en silencio. `extract_with_retry` trata `None` como fallo, reintenta una vez y, si persiste, lo registra en `failed_chunks`.
3. **Un LLM buscando alias libremente inventa uniones.** Nova 2 Lite propuso `deep learning` → `ai research` y `mila` → `montreal`. Con candidatos generados en código y reglas genéricas (ver "Diferencias de diseño"), esas uniones no pueden ocurrir.
4. **Las entidades compuestas rompían la resolución de alias.** Nova Pro extrajo `Pieridae and Nymphalidae` pese a la regla del prompt, y una versión anterior de la heurística unió las dos familias en esa entidad, mezclando sus cifras. Ahora una entidad compuesta nunca es candidata a alias.
5. **Nova Pro no fue claramente mejor que Nova 2 Lite.** Con el mismo abstract y la misma configuración, extrajo menos relaciones (30 frente a 44), generó más entidades compuestas y una vez devolvió una relación sin `subject` (la recuperó el reintento). Por costo, el modelo por defecto es Nova 2 Lite.
6. **Chunks más chicos dieron pocas relaciones más.** En la introducción del paper, pasar de 350 a 150 palabras por chunk aumentó las relaciones un 24% con 2,5 veces más llamadas al modelo. El valor por defecto es 350.
7. **El modelo identifica más entidades de las que conecta.** En el texto largo, más de un tercio de las entidades quedaron sin relaciones. `publish_graph` las separa en `unlinked_entities` para que el grafo quede limpio sin perder información.

## Despliegue en Amazon Bedrock AgentCore Runtime

Se despliega `kg_pipeline_docs`, la versión incremental. AgentCore Runtime ejecuta cada `runtimeSessionId` en su propia microVM y la mantiene viva entre invocaciones, así que todos los textos que mandás con el mismo id amplían el mismo grafo, que vive en memoria (`InMemorySessionService`). No hace falta base de datos.

```
deploy/invoke.py --boto3--> AgentCore Runtime (one microVM per runtimeSessionId)
                              └── agentcore_app.py -> Runner(kg_pipeline_docs) --> Amazon Bedrock (Nova 2 Lite)
```

Se usa el **despliegue directo de código** (un zip en S3) en lugar de un contenedor: no necesitás Docker ni ECR. El paquete pesa unos 72 MB comprimido, por debajo del límite de 250 MB.

> **Importante:** AgentCore cierra la microVM después de 15 minutos sin actividad o de 1 hora de vida (valores configurables en Terraform). Una invocación posterior con el mismo `--session-id` arranca un entorno nuevo y el grafo empieza vacío. Para conservarlo más tiempo hay que guardarlo fuera de la sesión, por ejemplo en S3.

### Qué crea Terraform

| Recurso | Para qué |
| :--- | :--- |
| Bucket S3 (privado, con versionado) | Guarda el zip. Al subir una versión nueva, el runtime se actualiza |
| Rol IAM de ejecución | Lo asume AgentCore. Permisos acotados: leer el zip, invocar solo Nova 2 Lite, escribir logs, trazas y métricas |
| `aws_bedrockagentcore_agent_runtime` | El runtime, con Python 3.13, red pública, ADOT para las trazas y una sesión que vive hasta 15 min sin actividad (1 h como máximo) |
| `terraform_data` (retención de logs) | Fija en 1 día la retención del log group del runtime con la AWS CLI y lo borra en `terraform destroy` |

¿Por qué la retención usa la AWS CLI y no un recurso `aws_cloudwatch_log_group`? AgentCore crea el log group (`/aws/bedrock-agentcore/runtimes/<runtime_id>-DEFAULT`) al mismo tiempo que el runtime, así que Terraform no puede crearlo antes (no conoce el id) ni después (ya existe). Y las políticas de retención a nivel cuenta exigen un mínimo de 30 días.

### Observabilidad: ver el recorrido de los agentes 

ADK genera spans de OpenTelemetry por cada agente, llamada al modelo y nodo del workflow. Para verlos en el dashboard de AgentCore (CloudWatch → GenAI Observability → Bedrock AgentCore) hacen falta dos cosas:

1. **ADOT en el agente.** Ya está configurado: el zip incluye `aws-opentelemetry-distro` y el runtime arranca con `opentelemetry-instrument`. Los spans van al stream `spans` del log group del runtime (telemetría unificada).
2. **CloudWatch Transaction Search activado en tu cuenta.** 
Se hace una sola vez por cuenta y región, desde la consola de CloudWatch: en el menú, *Application Signals* → *Transaction Search* → *Enable Transaction Search*, y marcá la opción de ingerir spans como logs estructurados. Indexar el 1% de los spans es gratis ([cómo activarlo](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Enable-TransactionSearch.html)). 
O la otra opción es activarlo desde AgentCore: el botón Configure del aviso "Observability has not been enabled".
Para verificar el estado:

```bash
# Read-only check: it does not enable anything
aws xray get-trace-segment-destination --region us-east-1
# "Destination": "CloudWatchLogs" -> Transaction Search is enabled
# "Destination": "XRay"           -> not enabled yet: enable it in the console
```

Transaction Search guarda los spans como logs, así que suma costo de ingesta en CloudWatch (muy bajo para una prueba).

### Pasos para el deploy en AWS

Activa la observabilidad opcionalmente explicado en la sección anterior.

Requisitos: Terraform 1.5 o superior, AWS CLI con credenciales configuradas y acceso a Amazon Nova en `us-east-1`. El usuario que despliega necesita permisos para crear recursos de S3, IAM y Bedrock AgentCore.

```bash
# 1. Build the deployment package (Linux arm64 wheels, runs on any OS)
python deploy/build_package.py

# 2. Create the infrastructure
cd infra/terraform
terraform init
terraform validate
terraform plan -out=plan.tfplan
terraform apply plan.tfplan

# 3. Invoke the agent: two texts of the same paper in ONE session
export AGENT_RUNTIME_ARN="$(terraform output -raw agent_runtime_arn)"
cd ../..
python deploy/invoke.py examples/papers/butterfly_migration_abstract.txt examples/papers/butterfly_migration_abstract_l.txt
```

`invoke.py` imprime cuántas relaciones tiene el grafo después de cada texto y guarda el resultado en `outputs/`. 
⭐ Para seguir ampliando el mismo grafo, pasá el mismo `--session-id` que imprimió la primera ejecución junto con el nuevo texto. Ejemplo:
```bash
python deploy/invoke.py --session-id kg-session-XXXX examples/papers/butterfly_migration_2.txt

```


Si cambiás el código, volvé a correr `python deploy/build_package.py` y `terraform apply`: Terraform sube el zip nuevo y actualiza el runtime.

Para probar el adaptador en local antes de desplegar:

```bash
python agentcore_app.py      # serves /invocations and /ping on port 8080

# In another terminal
curl -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-session" \
     -d '{"prompt": "Hinton left Google in 2023."}'
```

### Costos de la prueba

Desplegar y probar el agente tiene costo. Estas son estimaciones propias con las tarifas publicadas al momento de escribir esto (us-east-1). Antes de desplegar, revisá los precios vigentes en [AgentCore](https://aws.amazon.com/bedrock/agentcore/pricing/) y [Bedrock](https://aws.amazon.com/bedrock/pricing/).

| Componente | Cómo cobra | Estimación para la prueba |
| :--- | :--- | :--- |
| Amazon Nova 2 Lite | Por token: USD 0,00033 por 1.000 de entrada y USD 0,00275 por 1.000 de salida | Unos USD 0,01 por texto largo (aprox. 10.000 tokens de entrada y 1.500 de salida) |
| AgentCore Runtime | Por segundo de CPU y memoria consumidos durante la sesión, incluido el tiempo de espera hasta que la sesión expira | Menos de USD 0,01 por sesión de prueba. El tiempo de espera del modelo no consume CPU, pero la memoria se cobra mientras la sesión sigue viva |
| S3 | Almacenamiento estándar del zip (unos 72 MB por versión) | Menos de USD 0,01 por mes |
| CloudWatch Logs y trazas | Por ingesta y almacenamiento de logs y spans (con Transaction Search). La retención de 1 día limita el almacenamiento | Centavos para unas pocas decenas de ejecuciones |

Para una prueba de unas decenas de ejecuciones, el total debería quedar por debajo de USD 1. Lo que más pesa es la cantidad de tokens, que crece con el largo del texto.

## 🧹 LIMPIEZA

Para no generar costos después de la prueba:

```bash
cd infra/terraform
terraform destroy
```

`terraform destroy` borra el runtime, el rol, el bucket (incluido el zip) y el log group del runtime.

Transaction Search queda activado en la cuenta porque no lo gestiona Terraform. Si no lo vas a usar más, desactivalo desde la consola de CloudWatch para no seguir ingiriendo spans de otras aplicaciones.

## Licencia

Este proyecto está bajo la Licencia MIT. Consultá el archivo [LICENSE](LICENSE) para más detalles.

## Referencias
- [Google ADK vs Strands: guía técnica y despliegue en AWS](https://builder.aws.com/content/3MCC7aOXsK9abaDSMYZrZqZ1CuF/google-adk-vs-strands-guia-tecnica-y-despliegue-en-aws)
- [ADK: workflows basados en grafos](https://adk.dev/graphs/)
- [ADK: rutas, join y loops](https://adk.dev/graphs/routes/)
- [ADK: manejo de datos entre nodos](https://adk.dev/graphs/data-handling/)
- [ADK: interfaz web](https://adk.dev/runtime/web-interface/)
- [ADK: evaluación](https://adk.dev/evaluate/)
- [Strands Graph (versión original)](https://github.com/reinalau/strands-graph)
